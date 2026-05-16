"""Job fit scoring: Claude Haiku-powered evaluation with pre-filter and chain-of-thought.

Pre-filters jobs on salary/keywords/auto-reject phrases before any API call, then
runs a single chain-of-thought Claude Haiku call per job with prompt caching on
the system prompt and candidate profile blocks.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import anthropic

from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.tracker.db import get_tracker_connection

log = logging.getLogger(__name__)

MAX_JOBS_PER_CLAUDE_RUN = 50

# Haiku 4.5 pricing (USD per token)
_PRICE_INPUT       = 1.00 / 1_000_000   # $1.00/MTok
_PRICE_OUTPUT      = 5.00 / 1_000_000   # $5.00/MTok
_PRICE_CACHE_WRITE = 1.25 / 1_000_000   # $1.25/MTok (1.25× input, 5-min TTL)
_PRICE_CACHE_READ  = 0.10 / 1_000_000   # $0.10/MTok (0.1× input)

RAY_PROFILE = """Agil Raj — C&I Solar PM, Malaysia, ~5yr experience
STRENGTHS (ranked by SEA market rarity):
1. BESS AC/DC coupling + BMS/EMS optimization
2. CaaS/thermal assessment + heat pump evaluation
3. PPA/EPCC commercial admin — RM1M recovered
4. EHS — 2010 consecutive safe days, APAC #2
5. 30MWp C&I portfolio (rooftop/carpark/ground/BESS/EV)
6. BD — 89% win rate, 19 proposals, 17 contracts won
7. Multi-site delivery — 20% ahead of schedule
GOAL: Regional scope, bigger portfolio, hybrid role
FLOORS: MYR10k/SGD5k/AUD10k/EUR5k
AUTO-REJECT: admin-only PM, non-renewable, below floor
IGNORE AS GAP: PMP vs CAPM
EDGE CASES: vague JD=score5+REVIEW, old posting=ignore"""

_SYSTEM_PROMPT = (
    "You are a senior renewable energy recruitment specialist "
    "with deep SEA market knowledge. Evaluate jobs for Ray "
    "using chain-of-thought reasoning. Output only valid JSON."
)

# Pre-filter: at least one of these must appear in the JD
_REQUIRED_KEYWORDS = {
    "project", "manager", "engineer", "energy", "solar", "renewable",
    "asset", "development", "technical", "commercial", "regional",
    "portfolio", "delivery", "solutions", "caas", "eaas", "bess",
}

# Pre-filter: any of these phrases triggers an auto-reject
_AUTO_REJECT_PHRASES = [
    "purely administrative",
    "no technical",
    "data entry",
    "customer service",
    "non-renewable energy only",
]

# Salary floors used in the pre-filter salary check
_SALARY_FLOORS = {
    "MYR": 10_000,
    "SGD":  5_000,
    "AUD": 10_000,
    "EUR":  5_000,
}

_SCORE_JSON_TEMPLATE = """{
  "score": 0.0,
  "verdict": "STRONG APPLY|APPLY|REVIEW|SKIP",
  "role_type": "",
  "actual_scope": "",
  "strength_matches": [{"skill":"","requirement":"","weight":""}],
  "gaps": [{"gap":"","type":"HARD|SOFT|IGNORE","action":""}],
  "growth_trajectory_score": 0.0,
  "competitive_position": "",
  "standout_factor": "",
  "cover_letter_angle": "",
  "salary_assessment": "above_floor|below_floor|undisclosed",
  "auto_rejected": false,
  "rejection_reason": null
}"""


# ── Pre-filter ────────────────────────────────────────────────────────────────

def _prefilter(job: dict) -> tuple[bool, str]:
    """Return (should_skip, reason). True = drop this job before any Claude call."""
    jd = (job.get("full_description") or "").lower()

    # 1. Disclosed salary below floor
    if job.get("salary_disclosed") and job.get("salary_min_posted"):
        currency = (job.get("salary_currency") or "").upper()
        floor = _SALARY_FLOORS.get(currency)
        if floor and int(job["salary_min_posted"]) < floor:
            return (
                True,
                f"salary_below_floor:{currency}{job['salary_min_posted']}<{floor}",
            )

    # 2. No required keywords in JD
    if not any(kw in jd for kw in _REQUIRED_KEYWORDS):
        return True, "no_required_keywords"

    # 3. Auto-reject phrases
    for phrase in _AUTO_REJECT_PHRASES:
        if phrase in jd:
            return True, f"auto_reject_phrase:{phrase}"

    return False, ""


# ── Cost tracking ─────────────────────────────────────────────────────────────

def _calc_cost(usage) -> float:
    """Estimate USD cost from a Haiku 4.5 response usage object."""
    return (
        getattr(usage, "input_tokens", 0)                  * _PRICE_INPUT
        + getattr(usage, "output_tokens", 0)               * _PRICE_OUTPUT
        + getattr(usage, "cache_creation_input_tokens", 0) * _PRICE_CACHE_WRITE
        + getattr(usage, "cache_read_input_tokens", 0)     * _PRICE_CACHE_READ
    )


# ── Claude scoring ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Extract the first {...} JSON block from LLM output."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON block found in response")
    return json.loads(match.group())


def score_job_claude(client: anthropic.Anthropic, job: dict) -> dict:
    """Score one job with Claude Haiku. Returns parsed result dict with _url and _cost keys."""
    title    = job.get("title", "Unknown")
    company  = job.get("site") or job.get("company_name") or "Unknown"
    location = job.get("location", "Unknown")
    jd_text  = (job.get("full_description") or "")[:8000]

    if job.get("salary_min_posted"):
        cur    = job.get("salary_currency") or ""
        salary = f"{cur}{job['salary_min_posted']}"
        if job.get("salary_max_posted"):
            salary += f"-{job['salary_max_posted']}"
    else:
        salary = "undisclosed"

    user_content = (
        f"Candidate: {RAY_PROFILE}\n"
        f"Job: Title={title}, Company={company},\n"
        f"Location={location}, Salary={salary},\n"
        f"JD={jd_text}\n\n"
        "Reason through 6 steps then output JSON only:\n"
        "1. Role type and scope classification\n"
        "2. Semantic strength matches with weights\n"
        "3. Gap analysis (HARD/SOFT/IGNORE)\n"
        "4. Growth trajectory score 1-10\n"
        "5. Competitive position + standout factor\n"
        "6. Final verdict\n\n"
        f"Return ONLY this JSON:\n{_SCORE_JSON_TEMPLATE}"
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": RAY_PROFILE,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    cost = _calc_cost(response.usage)
    raw  = response.content[0].text

    try:
        parsed = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("JSON parse error '%s': %s | raw: %.200s", title, exc, raw)
        parsed = {
            "score": 0.0,
            "verdict": "REVIEW",
            "role_type": "",
            "actual_scope": "",
            "strength_matches": [],
            "gaps": [],
            "growth_trajectory_score": 0.0,
            "competitive_position": "",
            "standout_factor": f"parse_error:{str(exc)[:80]}",
            "cover_letter_angle": "",
            "salary_assessment": "undisclosed",
            "auto_rejected": False,
            "rejection_reason": None,
        }

    parsed["_url"]  = job.get("url", "")
    parsed["_cost"] = cost
    return parsed


# ── Main entry point ──────────────────────────────────────────────────────────

def run_scoring(limit: int = 0, rescore: bool = False) -> dict:
    """Pre-filter then score jobs with Claude Haiku.

    Returns:
        {
            "scored": int,
            "prefiltered": int,
            "errors": int,
            "elapsed": float,
            "estimated_cost_usd": float,
            "distribution": list,
        }
    """
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {
            "scored": 0, "prefiltered": 0, "errors": 0,
            "elapsed": 0.0, "estimated_cost_usd": 0.0, "distribution": [],
        }

    if not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    # ── Pre-filter stage ──────────────────────────────────────────────────────
    passed:     list[dict]  = []
    pf_records: list[tuple] = []  # (url, title, reason)

    for job in jobs:
        skip, reason = _prefilter(job)
        if skip:
            pf_records.append((job.get("url", ""), job.get("title", "?"), reason))
        else:
            passed.append(job)

    if len(passed) > MAX_JOBS_PER_CLAUDE_RUN:
        log.warning(
            "Capping at %d (had %d passing pre-filter)",
            MAX_JOBS_PER_CLAUDE_RUN, len(passed),
        )
        passed = passed[:MAX_JOBS_PER_CLAUDE_RUN]

    log.info(
        "Pre-filter: %d passed, %d dropped (total=%d)",
        len(passed), len(pf_records), len(jobs),
    )

    # ── Claude scoring ────────────────────────────────────────────────────────
    client     = anthropic.Anthropic()
    t0         = time.time()
    results:   list[dict] = []
    errors     = 0
    total_cost = 0.0

    for i, job in enumerate(passed):
        try:
            result = score_job_claude(client, job)
        except Exception as exc:
            log.error("Claude API error '%s': %s", job.get("title", "?"), exc)
            errors += 1
            result = {
                "_url": job.get("url", ""),
                "_cost": 0.0,
                "score": 0.0,
                "verdict": "REVIEW",
                "role_type": "", "actual_scope": "",
                "strength_matches": [], "gaps": [],
                "growth_trajectory_score": 0.0,
                "competitive_position": "",
                "standout_factor": f"api_error:{str(exc)[:80]}",
                "cover_letter_angle": "",
                "salary_assessment": "undisclosed",
                "auto_rejected": False,
                "rejection_reason": None,
            }

        total_cost += result["_cost"]
        results.append(result)

        log.info(
            "[%d/%d] %-50s | %s | score=%.1f | %s",
            i + 1, len(passed),
            job.get("title", "?")[:50],
            result.get("verdict", "?"),
            result.get("score", 0.0),
            (result.get("standout_factor") or "")[:60],
        )

    # ── Write to DB ───────────────────────────────────────────────────────────
    now          = datetime.now(timezone.utc).isoformat()
    conn_tracker = get_tracker_connection()

    # Mark pre-filtered jobs in both tables
    for url, title, reason in pf_records:
        conn.execute(
            "UPDATE jobs SET fit_score = 0, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (f"prefiltered: {reason}", now, url),
        )
        conn_tracker.execute(
            """UPDATE applications
               SET score_prefilter_result = ?,
                   score_auto_rejected    = 1,
                   updated_at             = ?
               WHERE job_url = ?""",
            (reason, now, url),
        )

    # Write Claude scoring results to both tables
    for r in results:
        url = r.get("_url", "")
        if not url:
            continue

        score = float(r.get("score") or 0.0)

        # jobs table — keeps existing fit_score / score_reasoning flow working
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (score, r.get("competitive_position", ""), now, url),
        )

        # applications table — all new Claude scoring fields
        conn_tracker.execute(
            """UPDATE applications
               SET fit_score                  = ?,
                   score_verdict              = ?,
                   score_role_type            = ?,
                   score_actual_scope         = ?,
                   score_strength_matches     = ?,
                   score_gaps                 = ?,
                   score_growth_trajectory    = ?,
                   score_competitive_position = ?,
                   score_standout_factor      = ?,
                   score_cover_letter_angle   = ?,
                   score_auto_rejected        = ?,
                   score_prefilter_result     = 'passed',
                   updated_at                 = ?
               WHERE job_url = ?""",
            (
                score,
                r.get("verdict"),
                r.get("role_type"),
                r.get("actual_scope"),
                json.dumps(r.get("strength_matches") or []),
                json.dumps(r.get("gaps") or []),
                r.get("growth_trajectory_score"),
                r.get("competitive_position"),
                r.get("standout_factor"),
                r.get("cover_letter_angle"),
                1 if r.get("auto_rejected") else 0,
                now,
                url,
            ),
        )

    conn.commit()
    conn_tracker.commit()

    elapsed = time.time() - t0

    # ── Run stats ─────────────────────────────────────────────────────────────
    try:
        conn_tracker.execute(
            """INSERT INTO run_stats
               (run_date, jobs_discovered, jobs_prefiltered, jobs_scored,
                jobs_strong_apply, jobs_apply, jobs_review, jobs_skip,
                estimated_cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                len(jobs),
                len(pf_records),
                len(results),
                sum(1 for r in results if r.get("verdict") == "STRONG APPLY"),
                sum(1 for r in results if r.get("verdict") == "APPLY"),
                sum(1 for r in results if r.get("verdict") == "REVIEW"),
                sum(1 for r in results if r.get("verdict") == "SKIP"),
                total_cost,
            ),
        )
        conn_tracker.commit()
    except Exception as _e:
        log.warning("Run stats write failed: %s", _e)

    print(
        f"Scored {len(results)} jobs | "
        f"Pre-filtered {len(pf_records)} | "
        f"Est cost: ${total_cost:.4f}"
    )

    # ── Score distribution ────────────────────────────────────────────────────
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    # ── Existing downstream hooks ─────────────────────────────────────────────
    try:
        from applypilot.tracker.queries import sync_scored_from_jobs
        sync_scored_from_jobs()
    except Exception as _e:
        log.warning("Tracker sync failed: %s", _e)

    try:
        from applypilot.salary.gate import run_salary_gate_all
        _s = run_salary_gate_all()
        log.info(
            "Salary gate: evaluated=%d dropped=%d flagged=%d errors=%d",
            _s["evaluated"], _s["dropped"], _s["flagged"], _s["errors"],
        )
    except Exception as _e:
        log.warning("Salary gate failed: %s", _e)

    return {
        "scored": len(results),
        "prefiltered": len(pf_records),
        "errors": errors,
        "elapsed": elapsed,
        "estimated_cost_usd": total_cost,
        "distribution": distribution,
    }
