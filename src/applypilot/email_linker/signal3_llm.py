"""Signal 3: LLM extraction + rapidfuzz company/title matching.

Always runs on every email regardless of Signal 1/2 results.

Steps:
  1. LLM extracts: company name, job title, intent, datetime mention, confidence
  2. rapidfuzz token_set_ratio matches extracted company+title against all
     applications in the tracker DB (company 60%, title 40% weighted blend)

Match strength thresholds (per spec):
  strong:   fuzzy score >= 85  → confidence +40
  moderate: fuzzy score >= 70  → confidence +25
  weak:     fuzzy score <  70  → confidence +10  (but still 'fired')

The LLM's own confidence integer is stored as llm_confidence for audit purposes
but is NOT used in fusion — only the fuzzy match strength drives the contribution.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz

from applypilot.llm import get_client
from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_STRONG_THRESHOLD = 85
_MODERATE_THRESHOLD = 70

_EXTRACT_PROMPT = """\
You are an email classifier for a job application tracker.

Analyse the email below and return a JSON object with EXACTLY these fields:
  "company":          string or null  — the company/recruiter name that sent this
  "title":            string or null  — the job title this email references
  "intent":           one of "interview", "rejection", "schedule", "offer", "other"
  "datetime_mention": string or null  — any interview/meeting date-time (ISO-8601 preferred)
  "confidence":       integer 0-100   — how confident you are in company + title

Reply with ONLY the JSON object. No markdown, no commentary.

Subject: {subject}
From:    {sender}
Body:    {body}
"""


def _call_llm(subject: str, sender_email: str, body_snippet: str) -> dict:
    """Call the configured LLM and parse structured extraction fields."""
    prompt = _EXTRACT_PROMPT.format(
        subject=subject or "(no subject)",
        sender=sender_email or "(unknown sender)",
        body=body_snippet or "(no body)",
    )
    client = get_client()
    # 8192 tokens gives gemini-2.5-flash enough room for thinking + JSON output
    raw = client.ask(prompt, temperature=0.0, max_tokens=8192)

    # Strip markdown fences if present
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        raw = m.group(0)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Signal3: LLM returned non-JSON: %s", raw[:200])
        return {}


def _fetch_applications() -> list[dict]:
    """Return id, job_title, company_name for all applications in tracker DB."""
    try:
        conn = get_tracker_connection()
        try:
            rows = conn.execute(
                "SELECT id, job_title, company_name FROM applications"
            ).fetchall()
        except Exception:
            conn = init_tracker_db()
            rows = conn.execute(
                "SELECT id, job_title, company_name FROM applications"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("Signal3 DB error fetching applications: %s", exc)
        return []


def _fuzzy_match(
    extracted_company: Optional[str],
    extracted_title: Optional[str],
    applications: list[dict],
) -> tuple[Optional[int], float, str]:
    """Return (best_app_id, fuzzy_score, match_strength).

    Weighted blend: company 60%, title 40% (drops to single field if one is None).
    match_strength: 'strong' | 'moderate' | 'weak' | 'none'
    """
    if not applications or (not extracted_company and not extracted_title):
        return None, 0.0, "none"

    best_id: Optional[int] = None
    best_score = 0.0

    for app in applications:
        company_score = 0.0
        title_score = 0.0

        if extracted_company and app.get("company_name"):
            company_score = fuzz.token_set_ratio(
                extracted_company.lower(), app["company_name"].lower()
            )
        if extracted_title and app.get("job_title"):
            title_score = fuzz.token_set_ratio(
                extracted_title.lower(), app["job_title"].lower()
            )

        # Weight blend — fall back to whichever field is available
        both = extracted_company and extracted_title
        combined = (
            company_score * 0.6 + title_score * 0.4
            if both
            else max(company_score, title_score)
        )

        if combined > best_score:
            best_score = combined
            best_id = app["id"]

    if best_score >= _STRONG_THRESHOLD:
        return best_id, best_score, "strong"
    if best_score >= _MODERATE_THRESHOLD:
        return best_id, best_score, "moderate"
    if best_score > 0:
        return best_id, best_score, "weak"
    return None, 0.0, "none"


def check_signal3(subject: str, sender_email: str, body_snippet: str) -> dict:
    """Run LLM extraction and fuzzy-match against all known applications.

    Always returns a result dict — never raises.

    Keys:
      application_id  int | None
      company         str | None
      title           str | None
      intent          str  ('interview'|'rejection'|'schedule'|'offer'|'other')
      datetime_str    str | None
      llm_confidence  int  (LLM's self-rated confidence, 0-100)
      match_strength  str  ('strong'|'moderate'|'weak'|'none')
      fired           bool
    """
    result: dict = {
        "application_id": None,
        "company": None,
        "title": None,
        "intent": "other",
        "datetime_str": None,
        "llm_confidence": 0,
        "match_strength": "none",
        "fired": False,
    }

    try:
        extracted = _call_llm(subject, sender_email, body_snippet)
    except Exception as exc:  # noqa: BLE001
        log.warning("Signal3 LLM call failed: %s", exc)
        return result

    result["company"] = extracted.get("company") or None
    result["title"] = extracted.get("title") or None
    result["intent"] = extracted.get("intent") or "other"
    result["datetime_str"] = extracted.get("datetime_mention") or None
    result["llm_confidence"] = int(extracted.get("confidence") or 0)

    applications = _fetch_applications()
    app_id, fuzzy_score, strength = _fuzzy_match(
        result["company"], result["title"], applications
    )

    result["application_id"] = app_id
    result["match_strength"] = strength
    result["fired"] = strength != "none" and app_id is not None

    if result["fired"]:
        log.debug(
            "Signal3 FIRED: company='%s' title='%s' → app_id=%s "
            "(strength=%s fuzzy=%.0f llm_conf=%d)",
            result["company"], result["title"], app_id,
            strength, fuzzy_score, result["llm_confidence"],
        )

    return result
