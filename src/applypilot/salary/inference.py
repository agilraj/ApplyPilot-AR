"""5-signal salary estimation engine for undisclosed job salaries.

Signals:
  1. Title benchmark    — known salary ranges by title keyword
  2. Company type       — multiplier based on inferred company type
  3. JD complexity      — LLM seniority scoring from job description
  4. Historical DB      — past applications with disclosed salaries for similar roles
  5. External data      — web search (Glassdoor / LinkedIn) attempt

Outputs: estimated_min, estimated_max, confidence (0-100), signals_used (list).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal 1 — Title benchmark (MYR base, scaled by PPP for other regions)
# ---------------------------------------------------------------------------

_TITLE_BENCHMARKS: dict[str, tuple[int, int]] = {
    # (min_MYR_monthly, max_MYR_monthly) approximate ranges
    "director": (18000, 35000),
    "head of": (15000, 28000),
    "vp": (20000, 40000),
    "vice president": (20000, 40000),
    "senior manager": (12000, 22000),
    "manager": (8000, 16000),
    "senior": (8000, 15000),
    "lead": (9000, 18000),
    "principal": (12000, 22000),
    "specialist": (6000, 12000),
    "analyst": (4000, 9000),
    "engineer": (6000, 14000),
    "consultant": (7000, 15000),
    "associate": (4000, 8000),
    "coordinator": (3500, 7000),
    "executive": (5000, 12000),
}


def _signal1_title(title: str, ppp_rate: float = 1.0) -> tuple[int, int, int]:
    """Return (est_min, est_max, confidence) from title keyword lookup."""
    title_lower = title.lower()
    for kw, (lo, hi) in _TITLE_BENCHMARKS.items():
        if kw in title_lower:
            return int(lo * ppp_rate), int(hi * ppp_rate), 30
    # Fallback range
    return int(6000 * ppp_rate), int(14000 * ppp_rate), 10


# ---------------------------------------------------------------------------
# Signal 2 — Company type multiplier
# ---------------------------------------------------------------------------

_COMPANY_TYPE_MULTIPLIERS: dict[str, float] = {
    "mnc": 1.3,
    "mid": 1.0,
    "sme": 0.8,
    "glc": 0.9,
    "startup": 0.9,
}

_COMPANY_TYPE_KEYWORDS: dict[str, list[str]] = {
    "mnc": ["plc", "inc.", "corp", "corporation", "global", "international", "group"],
    "glc": ["berhad", "bhd", "sdn", "petronas", "telekom", "tenaga", "maybank"],
    "startup": ["startup", "seed", "series a", "series b", "venture"],
}


def _signal2_company(company_name: str, company_type: Optional[str] = None) -> tuple[float, int]:
    """Return (multiplier, confidence)."""
    if company_type and company_type.lower() in _COMPANY_TYPE_MULTIPLIERS:
        return _COMPANY_TYPE_MULTIPLIERS[company_type.lower()], 20

    name_lower = (company_name or "").lower()
    for ctype, keywords in _COMPANY_TYPE_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return _COMPANY_TYPE_MULTIPLIERS[ctype], 15

    return 1.0, 5  # unknown company type


# ---------------------------------------------------------------------------
# Signal 3 — JD complexity via LLM seniority scoring
# ---------------------------------------------------------------------------

_JD_COMPLEXITY_PROMPT = """You are a compensation analyst. Read this job description and return ONLY a JSON object with these fields:
{
  "seniority_score": <int 1-10>,
  "years_exp_required": <int or null>,
  "team_size_mentioned": <bool>,
  "pl_responsibility": <bool>,
  "regional_scope": <bool>,
  "estimated_level": "<junior|mid|senior|lead|manager|director>"
}
Respond with valid JSON only, no other text."""


def _signal3_jd_complexity(
    description: str,
) -> tuple[float, int]:
    """Return (seniority_multiplier, confidence) from LLM JD analysis.

    Seniority multiplier: 0.7 (junior) to 1.4 (director).
    Falls back to 1.0 with 0 confidence if LLM is unavailable.
    """
    if not description:
        return 1.0, 0

    try:
        import json as _json
        from applypilot.llm import get_client

        client = get_client()
        messages = [
            {"role": "system", "content": _JD_COMPLEXITY_PROMPT},
            {"role": "user", "content": description[:3000]},
        ]
        raw = client.chat(messages, max_tokens=200, temperature=0.1)
        # Extract JSON from response (may have surrounding text)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return 1.0, 0
        data = _json.loads(match.group())

        level = data.get("estimated_level", "mid")
        multiplier_map = {
            "junior": 0.75,
            "mid": 1.0,
            "senior": 1.15,
            "lead": 1.25,
            "manager": 1.35,
            "director": 1.45,
        }
        multiplier = multiplier_map.get(level, 1.0)
        # Bonus for P&L / regional scope
        if data.get("pl_responsibility") or data.get("regional_scope"):
            multiplier = min(multiplier * 1.1, 1.5)
        return multiplier, 20
    except Exception as exc:
        log.debug("Signal 3 LLM failed: %s", exc)
        return 1.0, 0


# ---------------------------------------------------------------------------
# Signal 4 — Historical DB lookup
# ---------------------------------------------------------------------------

def _signal4_historical(
    title: str,
    country: Optional[str],
    company_type: Optional[str],
) -> tuple[Optional[int], Optional[int], int]:
    """Return (est_min, est_max, confidence) from past disclosed applications.

    Queries the tracker applications table for similar title + location combos.
    Returns (None, None, 0) if no historical data is available.
    """
    try:
        from applypilot.tracker.db import get_tracker_connection

        conn = get_tracker_connection()
        title_words = [w for w in title.lower().split() if len(w) > 3]
        if not title_words:
            return None, None, 0

        like_clause = " OR ".join(
            f"LOWER(job_title) LIKE ?" for _ in title_words
        )
        params = [f"%{w}%" for w in title_words]

        if country:
            like_clause += " AND job_location_country = ?"
            params.append(country)

        rows = conn.execute(
            f"SELECT salary_min_posted, salary_max_posted FROM applications "
            f"WHERE salary_disclosed = 1 AND salary_min_posted IS NOT NULL "
            f"AND ({like_clause}) "
            f"ORDER BY created_at DESC LIMIT 10",
            params,
        ).fetchall()

        if not rows:
            return None, None, 0

        mins = [r[0] for r in rows if r[0]]
        maxs = [r[1] for r in rows if r[1]]
        if not mins:
            return None, None, 0

        est_min = int(sum(mins) / len(mins))
        est_max = int(sum(maxs) / len(maxs)) if maxs else est_min
        confidence = min(20, 5 * len(mins))  # up to 20 pts for 4+ matches
        return est_min, est_max, confidence
    except Exception as exc:
        log.debug("Signal 4 historical DB failed: %s", exc)
        return None, None, 0


# ---------------------------------------------------------------------------
# Signal 5 — External web search (best-effort)
# ---------------------------------------------------------------------------

def _signal5_web(title: str, country: Optional[str]) -> tuple[Optional[int], Optional[int], int]:
    """Return (est_min, est_max, confidence) from a web salary search.

    Performs a simple search query and parses any salary ranges found.
    Returns (None, None, 0) if search is unavailable or returns no salary data.
    """
    try:
        import urllib.parse
        import urllib.request

        query = f"{title} salary {country or ''} glassdoor site:glassdoor.com OR site:linkedin.com"
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}"

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="ignore")

        # Simple regex to find salary-like patterns (e.g. "10,000 MYR" or "SGD 5,000")
        pattern = r"(?:MYR|SGD|AUD|EUR|USD|GBP)\s*[\d,]+|[\d,]+\s*(?:MYR|SGD|AUD|EUR|USD|GBP)"
        matches = re.findall(pattern, body)

        amounts = []
        for m in matches:
            digits = re.sub(r"[^\d]", "", m)
            if digits and 1000 <= int(digits) <= 500000:
                amounts.append(int(digits))

        if len(amounts) < 2:
            return None, None, 0

        amounts.sort()
        est_min = amounts[len(amounts) // 4]
        est_max = amounts[3 * len(amounts) // 4]
        return est_min, est_max, 10
    except Exception as exc:
        log.debug("Signal 5 web search failed: %s", exc)
        return None, None, 0


# ---------------------------------------------------------------------------
# Engine: combine all signals
# ---------------------------------------------------------------------------

def infer_salary(
    title: str,
    description: str,
    company_name: str,
    country: Optional[str] = None,
    is_remote: bool = False,
    company_type: Optional[str] = None,
    ppp_rate: float = 1.0,
) -> dict:
    """Run all 5 signals and return a blended salary estimate.

    Returns:
      {
        "estimated_min": int,
        "estimated_max": int,
        "confidence": int (0-100),
        "signals_used": list[str],
        "notes": str,
      }
    """
    signals_used: list[str] = []
    confidence_parts: list[int] = []
    min_estimates: list[int] = []
    max_estimates: list[int] = []

    # Signal 1 — Title
    s1_min, s1_max, s1_conf = _signal1_title(title, ppp_rate)
    if s1_conf > 0:
        min_estimates.append(s1_min)
        max_estimates.append(s1_max)
        confidence_parts.append(s1_conf)
        signals_used.append("title_benchmark")

    # Signal 2 — Company type
    s2_mult, s2_conf = _signal2_company(company_name, company_type)
    if s2_conf > 0:
        confidence_parts.append(s2_conf)
        signals_used.append("company_type")
        # Apply multiplier to Signal 1 estimates
        if min_estimates:
            min_estimates = [int(v * s2_mult) for v in min_estimates]
            max_estimates = [int(v * s2_mult) for v in max_estimates]

    # Signal 3 — JD complexity
    s3_mult, s3_conf = _signal3_jd_complexity(description)
    if s3_conf > 0:
        confidence_parts.append(s3_conf)
        signals_used.append("jd_complexity")
        if min_estimates:
            min_estimates = [int(v * s3_mult) for v in min_estimates]
            max_estimates = [int(v * s3_mult) for v in max_estimates]

    # Signal 4 — Historical
    s4_min, s4_max, s4_conf = _signal4_historical(title, country, company_type)
    if s4_conf > 0 and s4_min:
        min_estimates.append(s4_min)
        max_estimates.append(s4_max or s4_min)
        confidence_parts.append(s4_conf)
        signals_used.append("historical_db")

    # Signal 5 — Web
    s5_min, s5_max, s5_conf = _signal5_web(title, country)
    if s5_conf > 0 and s5_min:
        min_estimates.append(s5_min)
        max_estimates.append(s5_max or s5_min)
        confidence_parts.append(s5_conf)
        signals_used.append("web_search")

    # Blend
    if not min_estimates:
        return {
            "estimated_min": 0,
            "estimated_max": 0,
            "confidence": 0,
            "signals_used": [],
            "notes": "no signals produced estimates",
        }

    est_min = int(sum(min_estimates) / len(min_estimates))
    est_max = int(sum(max_estimates) / len(max_estimates))
    confidence = min(100, sum(confidence_parts))

    return {
        "estimated_min": est_min,
        "estimated_max": est_max,
        "confidence": confidence,
        "signals_used": signals_used,
        "notes": f"blended from {len(signals_used)} signal(s)",
    }
