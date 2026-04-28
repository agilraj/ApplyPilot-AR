"""Classify a job's raw location string into a location tier.

Tiers: remote | preferred | acceptable | unknown | excluded

Results are written to the applications table in the tracker DB.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from applypilot.location.config_loader import (
    get_acceptable_locations,
    get_excluded_locations,
    get_hybrid_policy,
    get_location_scores,
    get_preferred_locations,
    is_remote_accepted,
)

log = logging.getLogger(__name__)

# Keywords that indicate a remote or fully-flexible role
_REMOTE_KEYWORDS = frozenset([
    "remote", "work from home", "wfh", "anywhere", "distributed",
    "fully remote", "100% remote", "remote-first", "remote first",
    "virtual", "home-based", "home based",
])

_HYBRID_KEYWORDS = frozenset([
    "hybrid", "flexible", "partially remote", "partial remote",
])


def _normalise(text: str) -> str:
    return text.lower().strip()


def _is_remote(location_raw: str) -> bool:
    norm = _normalise(location_raw)
    return any(kw in norm for kw in _REMOTE_KEYWORDS)


def _is_hybrid(location_raw: str) -> tuple[bool, Optional[int]]:
    """Return (is_hybrid, days_per_week_mentioned)."""
    norm = _normalise(location_raw)
    if not any(kw in norm for kw in _HYBRID_KEYWORDS):
        return False, None
    # Try to extract "X days" from the string
    match = re.search(r"(\d)\s*day", norm)
    days = int(match.group(1)) if match else None
    return True, days


def _country_match(raw: str, country: str) -> bool:
    norm = _normalise(raw)
    return _normalise(country) in norm


def _city_match(raw: str, city: str) -> bool:
    norm = _normalise(raw)
    return _normalise(city) in norm


def classify_location(location_raw: Optional[str]) -> dict:
    """Classify a raw location string into a tier dict.

    Returns a dict with keys:
      tier                  — remote | preferred | acceptable | unknown | excluded
      location_score_value  — integer 0-10
      job_is_remote         — bool
      job_is_hybrid         — bool
      hybrid_days_mentioned — int or None
      job_location_city     — str or None
      job_location_country  — str or None
      relocation_required   — bool
      relocation_flag       — str or None
      notes                 — str (human-readable reason)
    """
    if not location_raw:
        scores = get_location_scores()
        return {
            "tier": "unknown",
            "location_score_value": scores.get("unknown_ambiguous", 3),
            "job_is_remote": False,
            "job_is_hybrid": False,
            "hybrid_days_mentioned": None,
            "job_location_city": None,
            "job_location_country": None,
            "relocation_required": False,
            "relocation_flag": None,
            "notes": "no location data",
        }

    scores = get_location_scores()
    hybrid_flag, hybrid_days = _is_hybrid(location_raw)

    # --- Remote check ---
    if _is_remote(location_raw) and is_remote_accepted():
        return {
            "tier": "remote",
            "location_score_value": scores.get("remote", 10),
            "job_is_remote": True,
            "job_is_hybrid": hybrid_flag,
            "hybrid_days_mentioned": hybrid_days,
            "job_location_city": None,
            "job_location_country": None,
            "relocation_required": False,
            "relocation_flag": None,
            "notes": "remote role accepted",
        }

    # --- Excluded check ---
    for excl in get_excluded_locations():
        if _country_match(location_raw, excl) or _city_match(location_raw, excl):
            return {
                "tier": "excluded",
                "location_score_value": scores.get("excluded", 0),
                "job_is_remote": False,
                "job_is_hybrid": hybrid_flag,
                "hybrid_days_mentioned": hybrid_days,
                "job_location_city": None,
                "job_location_country": excl,
                "relocation_required": False,
                "relocation_flag": "excluded_location",
                "notes": f"excluded location: {excl}",
            }

    # --- Preferred check ---
    for pref in get_preferred_locations():
        city = pref.get("city", "")
        country = pref.get("country", "")
        if _city_match(location_raw, city) or _country_match(location_raw, country):
            reloc = pref.get("relocation_required", False)
            return {
                "tier": "preferred",
                "location_score_value": scores.get("preferred_city_within_commute", 10),
                "job_is_remote": False,
                "job_is_hybrid": hybrid_flag,
                "hybrid_days_mentioned": hybrid_days,
                "job_location_city": city or None,
                "job_location_country": country or None,
                "relocation_required": reloc,
                "relocation_flag": pref.get("relocation_condition") if reloc else None,
                "notes": f"preferred location match: {city}, {country}",
            }

    # --- Acceptable check ---
    for acc in get_acceptable_locations():
        country = acc.get("country", "")
        if _country_match(location_raw, country):
            reloc = acc.get("relocation_required", True)
            return {
                "tier": "acceptable",
                "location_score_value": (
                    scores.get("acceptable_country_relocation", 5)
                    if reloc
                    else scores.get("acceptable_country_no_relocation", 8)
                ),
                "job_is_remote": False,
                "job_is_hybrid": hybrid_flag,
                "hybrid_days_mentioned": hybrid_days,
                "job_location_city": None,
                "job_location_country": country,
                "relocation_required": reloc,
                "relocation_flag": acc.get("relocation_condition") if reloc else None,
                "notes": f"acceptable location match: {country}",
            }

    # --- Unknown ---
    return {
        "tier": "unknown",
        "location_score_value": scores.get("unknown_ambiguous", 3),
        "job_is_remote": False,
        "job_is_hybrid": hybrid_flag,
        "hybrid_days_mentioned": hybrid_days,
        "job_location_city": None,
        "job_location_country": None,
        "relocation_required": False,
        "relocation_flag": "unknown_location_flagged_for_review",
        "notes": f"no match found for: {location_raw!r}",
    }


def classify_job(job_url: str, location_raw: Optional[str]) -> dict:
    """Classify a single job and persist results to the tracker DB.

    Returns the classification dict.
    """
    result = classify_location(location_raw)

    try:
        from applypilot.tracker.queries import update_application
        update_application(
            job_url,
            job_location_raw=location_raw,
            job_location_city=result["job_location_city"],
            job_location_country=result["job_location_country"],
            job_is_remote=result["job_is_remote"],
            job_is_hybrid=result["job_is_hybrid"],
            hybrid_days_mentioned=result["hybrid_days_mentioned"],
            location_tier=result["tier"],
            location_score_value=result["location_score_value"],
            relocation_required=result["relocation_required"],
            relocation_flag=result["relocation_flag"],
        )
    except Exception as exc:
        log.warning("Location classify DB write failed for %s: %s", job_url[:60], exc)

    return result


def classify_all_discovered() -> dict:
    """Classify location for all jobs in the tracker that lack a location_tier.

    Returns {"classified": int, "excluded": int, "errors": int}.
    """
    from applypilot.database import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT url, location FROM jobs WHERE location IS NOT NULL"
    ).fetchall()

    classified = 0
    excluded = 0
    errors = 0

    for row in rows:
        url = row["url"] if hasattr(row, "keys") else row[0]
        location_raw = row["location"] if hasattr(row, "keys") else row[1]
        if not url:
            continue
        try:
            result = classify_job(url, location_raw)
            classified += 1
            if result["tier"] == "excluded":
                excluded += 1
                log.info("Location: EXCLUDED job %s (%s)", url[:60], location_raw)
            elif result["tier"] == "unknown":
                log.info("Location: UNKNOWN (flagged) job %s (%s)", url[:60], location_raw)
        except Exception as exc:
            log.warning("Location classify error for %s: %s", url[:60], exc)
            errors += 1

    log.info(
        "Location classifier: classified=%d excluded=%d errors=%d",
        classified, excluded, errors,
    )
    return {"classified": classified, "excluded": excluded, "errors": errors}
