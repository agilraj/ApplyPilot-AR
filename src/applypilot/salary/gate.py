"""Salary gate decision logic.

Evaluates a job against the user's salary floors and targets.
Writes results to the tracker DB. Returns gate result and salary score.

Gate results:
  above_target          — disclosed and above target
  at_target             — disclosed and at target
  above_floor           — disclosed, above floor but below target
  borderline            — disclosed, within 10% below floor (flag, proceed)
  below_floor           — disclosed, below floor (drop)
  undisclosed_proceed   — inferred with sufficient confidence
  undisclosed_flag      — inferred with low confidence (flag, proceed)
  undisclosed_no_data   — no inference possible (flag, proceed with low score)
  sponsorship_mismatch  — employer can't sponsor (drop)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from applypilot.salary.config_loader import (
    get_floor_and_target,
    get_salary_fit_scores,
    get_undisclosed_policy,
    load_salary_config,
    load_scoring_weights,
    load_work_auth,
)
from applypilot.salary.inference import infer_salary
from applypilot.salary.ppp import get_ppp_rate

log = logging.getLogger(__name__)

# Currency symbols and codes used when parsing raw salary strings
_CURRENCY_MAP: dict[str, str] = {
    "rm": "MYR", "myr": "MYR",
    "sgd": "SGD", "s$": "SGD",
    "aud": "AUD", "a$": "AUD",
    "eur": "EUR", "€": "EUR",
    "gbp": "GBP", "£": "GBP",
    "usd": "USD", "$": "USD",
}


def _parse_salary(salary_raw: Optional[str]) -> tuple[bool, Optional[str], Optional[int], Optional[int]]:
    """Parse a raw salary string into (disclosed, currency, min, max).

    Returns (False, None, None, None) when no numeric salary is found.
    """
    if not salary_raw:
        return False, None, None, None

    text = salary_raw.lower().strip()

    # Detect currency
    currency = None
    for sym, code in _CURRENCY_MAP.items():
        if sym in text:
            currency = code
            break

    # Extract all numbers (handles "10,000 - 15,000" or "10k" etc.)
    clean = re.sub(r"[,\s]", "", text)
    numbers = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*k?", clean):
        val = float(m.group(1))
        if "k" in clean[m.end():m.end() + 1]:
            val *= 1000
        # Plausible monthly range: 1000 to 500000
        if 1000 <= val <= 500000:
            numbers.append(int(val))

    if not numbers:
        return False, currency, None, None

    numbers.sort()
    salary_min = numbers[0]
    salary_max = numbers[-1] if len(numbers) > 1 else numbers[0]

    # If both numbers are the same it might be an annual figure — convert to monthly
    # Heuristic: if value > 100_000 treat as annual
    if salary_min > 100_000:
        salary_min = salary_min // 12
        salary_max = salary_max // 12

    return True, currency, salary_min, salary_max


def _salary_fit_score_for_gate(gate_result: str, inference_confidence: int = 0) -> float:
    """Map a gate_result to a 0-10 salary fit sub-score."""
    fit_scores = get_salary_fit_scores()
    mapping = {
        "above_target":         fit_scores.get("disclosed_above_target", 10),
        "at_target":            fit_scores.get("disclosed_at_target", 8),
        "above_floor":          fit_scores.get("disclosed_above_floor_below_target", 6),
        "borderline":           fit_scores.get("disclosed_borderline", 3),
        "below_floor":          fit_scores.get("disclosed_below_floor", 0),
        "sponsorship_mismatch": 0,
    }
    if gate_result in mapping:
        return float(mapping[gate_result])

    # Undisclosed — map by confidence band
    if gate_result in ("undisclosed_proceed", "undisclosed_flag", "undisclosed_no_data"):
        if inference_confidence >= 80:
            return float(fit_scores.get("undisclosed_confidence_80_plus", 7))
        if inference_confidence >= 60:
            return float(fit_scores.get("undisclosed_confidence_60_to_79", 5))
        if inference_confidence >= 40:
            return float(fit_scores.get("undisclosed_confidence_40_to_59", 3))
        if inference_confidence > 0:
            return float(fit_scores.get("undisclosed_confidence_below_40", 1))
        return float(fit_scores.get("undisclosed_no_inference", 2))

    return 2.0  # safe fallback


def _check_sponsorship(country: Optional[str], description: Optional[str]) -> bool:
    """Return True if the job requires sponsorship that can't be provided.

    Checks work_auth.json against keywords in the description.
    """
    if not country:
        return False

    work_auth = load_work_auth()
    auth = work_auth.get(country, {})
    our_sponsorship_needed: bool = auth.get("requires_sponsorship", False)

    if not our_sponsorship_needed:
        return False  # No sponsorship issue for this country

    # Check if the JD explicitly says "no sponsorship"
    if description:
        no_sponsor_phrases = [
            "no sponsorship", "must be authorized", "cannot sponsor",
            "will not sponsor", "must have right to work", "no visa",
            "citizens only", "permanent residents only",
        ]
        desc_lower = description.lower()
        if any(ph in desc_lower for ph in no_sponsor_phrases):
            return True

    return False


def evaluate_salary(
    job_url: str,
    title: str,
    company_name: str,
    salary_raw: Optional[str],
    description: Optional[str],
    location_country: Optional[str],
    is_remote: bool = False,
    resume_score: Optional[float] = None,
    company_type: Optional[str] = None,
) -> dict:
    """Evaluate a job against salary floor/target rules.

    Writes salary fields to the tracker DB.

    Returns a dict with:
      gate_result         str
      salary_score        float (0-10)
      salary_disclosed    bool
      salary_min_posted   int or None
      salary_max_posted   int or None
      salary_floor_applied int
      salary_target_applied int
      inference_confidence int (0-100)
      estimated_min       int or None
      estimated_max       int or None
      ppp_rate_used       float
      drop                bool  — True if pipeline should exclude this job
      flag                bool  — True if needs human review
    """
    cfg = load_salary_config()
    ppp_rate = get_ppp_rate(location_country or "") if not is_remote else 1.0
    floor, target = get_floor_and_target(location_country, is_remote)
    undisclosed_policy = get_undisclosed_policy()
    weights = load_scoring_weights()
    relevance_threshold: float = weights.get("relevance_override_score_threshold", 9.0)

    result: dict = {
        "gate_result": None,
        "salary_score": 2.0,
        "salary_disclosed": False,
        "salary_min_posted": None,
        "salary_max_posted": None,
        "salary_currency": None,
        "salary_floor_applied": floor,
        "salary_target_applied": target,
        "inference_confidence": 0,
        "estimated_min": None,
        "estimated_max": None,
        "ppp_rate_used": ppp_rate,
        "drop": False,
        "flag": False,
    }

    # --- Sponsorship check ---
    if _check_sponsorship(location_country, description):
        result.update({
            "gate_result": "sponsorship_mismatch",
            "salary_score": 0.0,
            "drop": True,
            "flag": False,
        })
        _write_to_tracker(job_url, result)
        return result

    # --- Parse salary ---
    disclosed, currency, sal_min, sal_max = _parse_salary(salary_raw)
    result["salary_disclosed"] = disclosed
    result["salary_currency"] = currency
    result["salary_min_posted"] = sal_min
    result["salary_max_posted"] = sal_max

    if disclosed and sal_min is not None:
        # For Europe PPP countries, adjust the floor comparison
        effective_floor = floor
        region_cfg = cfg.get("regions", {}).get(location_country or "", {})
        if region_cfg.get("method") == "ppp_adjusted_per_country" and location_country:
            effective_floor = int(
                cfg["regions"]["Europe"]["minimum"] * get_ppp_rate(location_country)
            )

        if sal_min >= target:
            gate = "above_target"
        elif sal_min >= floor:
            gate = "above_floor" if sal_min < target else "at_target"
        elif sal_min >= floor * 0.90:
            gate = "borderline"
            result["flag"] = True
        else:
            gate = "below_floor"
            result["drop"] = True

        result["gate_result"] = gate
        result["salary_score"] = _salary_fit_score_for_gate(gate)
        _write_to_tracker(job_url, result)
        return result

    # --- Undisclosed: run inference ---
    inference = infer_salary(
        title=title,
        description=description or "",
        company_name=company_name,
        country=location_country,
        is_remote=is_remote,
        company_type=company_type,
        ppp_rate=ppp_rate,
    )
    confidence: int = inference["confidence"]
    est_min: Optional[int] = inference.get("estimated_min") or None
    est_max: Optional[int] = inference.get("estimated_max") or None

    result["inference_confidence"] = confidence
    result["estimated_min"] = est_min
    result["estimated_max"] = est_max

    min_confidence: int = undisclosed_policy.get("min_confidence_to_proceed", 40)
    override_resume: float = undisclosed_policy.get("relevance_override_resume_score", 9.0)
    override_conf: int = undisclosed_policy.get("relevance_override_min_confidence", 40)

    # Relevance override
    if (
        resume_score is not None
        and resume_score >= override_resume
        and confidence >= override_conf
    ):
        gate = "undisclosed_proceed"
        result["flag"] = True  # "salary TBC at interview"
    elif confidence >= min_confidence:
        gate = "undisclosed_proceed"
    elif confidence > 0:
        gate = "undisclosed_flag"
        result["flag"] = True
    else:
        gate = "undisclosed_no_data"
        result["flag"] = True

    result["gate_result"] = gate
    result["salary_score"] = _salary_fit_score_for_gate(gate, confidence)
    _write_to_tracker(job_url, result)
    return result


def _write_to_tracker(job_url: str, result: dict) -> None:
    """Persist salary gate fields to the applications table."""
    try:
        from applypilot.tracker.queries import update_application
        update_application(
            job_url,
            salary_disclosed=result.get("salary_disclosed", False),
            salary_currency=result.get("salary_currency"),
            salary_min_posted=result.get("salary_min_posted"),
            salary_max_posted=result.get("salary_max_posted"),
            salary_floor_applied=result.get("salary_floor_applied"),
            salary_target_applied=result.get("salary_target_applied"),
            salary_gate_result=result.get("gate_result"),
            salary_inference_confidence=result.get("inference_confidence"),
            salary_estimated_min=result.get("estimated_min"),
            salary_estimated_max=result.get("estimated_max"),
            ppp_rate_used=result.get("ppp_rate_used"),
            salary_score=result.get("salary_score"),
        )
    except Exception as exc:
        log.warning("Salary gate DB write failed for %s: %s", job_url[:60], exc)


def run_salary_gate_all() -> dict:
    """Run salary gate over all scored jobs that lack a salary_gate_result.

    Returns {"evaluated": int, "dropped": int, "flagged": int, "errors": int}.
    """
    from applypilot.database import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT url, title, site, location, salary, full_description, fit_score "
        "FROM jobs WHERE fit_score IS NOT NULL"
    ).fetchall()

    evaluated = 0
    dropped = 0
    flagged = 0
    errors = 0

    for row in rows:
        url = row["url"] if hasattr(row, "keys") else row[0]
        title = (row["title"] if hasattr(row, "keys") else row[1]) or ""
        company = (row["site"] if hasattr(row, "keys") else row[2]) or ""
        location_raw = (row["location"] if hasattr(row, "keys") else row[3]) or ""
        salary_raw = (row["salary"] if hasattr(row, "keys") else row[4])
        description = (row["full_description"] if hasattr(row, "keys") else row[5])
        fit_score = row["fit_score"] if hasattr(row, "keys") else row[6]

        if not url:
            continue

        # Guess country from location_raw (simple extraction)
        country = _extract_country(location_raw)
        is_remote = "remote" in location_raw.lower() if location_raw else False

        try:
            res = evaluate_salary(
                job_url=url,
                title=title,
                company_name=company,
                salary_raw=salary_raw,
                description=description,
                location_country=country,
                is_remote=is_remote,
                resume_score=float(fit_score) if fit_score else None,
            )
            evaluated += 1
            if res["drop"]:
                dropped += 1
                log.info("Salary gate DROP: %s (%s)", url[:60], res["gate_result"])
            elif res["flag"]:
                flagged += 1
                log.info("Salary gate FLAG: %s (%s)", url[:60], res["gate_result"])
        except Exception as exc:
            log.warning("Salary gate error for %s: %s", url[:60], exc)
            errors += 1

    log.info(
        "Salary gate: evaluated=%d dropped=%d flagged=%d errors=%d",
        evaluated, dropped, flagged, errors,
    )
    return {"evaluated": evaluated, "dropped": dropped, "flagged": flagged, "errors": errors}


def _extract_country(location_raw: str) -> Optional[str]:
    """Best-effort country extraction from a raw location string."""
    if not location_raw:
        return None

    # Known country names to scan for (ordered longest first to avoid partial matches)
    known = [
        "Malaysia", "Singapore", "Australia", "Germany", "Netherlands",
        "United Kingdom", "UK", "Sweden", "Thailand", "Indonesia",
        "Philippines", "Vietnam", "Switzerland", "Norway", "Denmark",
        "France", "Belgium", "Austria", "Spain", "Portugal",
        "Poland", "Czech Republic", "Hungary", "UAE",
        "United Arab Emirates", "United States", "USA",
    ]
    loc_lower = location_raw.lower()
    for country in known:
        if country.lower() in loc_lower:
            # Normalise UK variants
            if country in ("UK", "United Kingdom"):
                return "UK"
            if country in ("UAE", "United Arab Emirates"):
                return "UAE"
            if country in ("USA", "United States"):
                return "United States"
            return country
    return None
