"""Fusion layer: confidence scoring, action routing, and DB persistence.

Spec logic (Section 9, Feature B):

  Priority:   Signal 2 > Signal 1 > Signal 3 (alone)
  Confidence: base weight from firing signal(s) + agreement bonus (+20) if
              Signal 3 agrees with primary
  Conflict:   Signal 1 fires AND Signal 3 fires AND disagree → confidence = 0,
              flag for manual review (Signal 2 vs Signal 3 disagreement is
              ignored — tagged address is deterministic)

  Actions:
    >= 60  → auto_link  (link and update application/status)
    30–59  → link_flag  (link but flag for human review)
    <  30  → hold       (do not link, notify user)

On auto_link + interview detected:
    Update application.status → 'interview_scheduled'
    Store interview_datetime, interview_type
    Log status_history entry

On auto_link + rejection detected:
    Update application.status → 'rejected'
    Log status_history entry
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GMAIL_CONFIG = _PROJECT_ROOT / "config" / "gmail.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    with open(_GMAIL_CONFIG) as f:
        return json.load(f)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    conn = get_tracker_connection()
    try:
        conn.execute("SELECT 1 FROM email_events LIMIT 1")
    except Exception:
        conn = init_tracker_db()
    return conn


def _text_contains_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _is_interview_email(intent: Optional[str], subject: str, body: str, cfg: dict) -> bool:
    if intent in ("interview", "schedule"):
        return True
    return _text_contains_keywords(
        (subject or "") + " " + (body or ""),
        cfg.get("interview_keywords", []),
    )


def _is_rejection_email(intent: Optional[str], subject: str, body: str, cfg: dict) -> bool:
    if intent == "rejection":
        return True
    return _text_contains_keywords(
        (subject or "") + " " + (body or ""),
        cfg.get("rejection_keywords", []),
    )


# ---------------------------------------------------------------------------
# DB writers
# ---------------------------------------------------------------------------

def _save_email_event(
    conn,
    email_data: dict,
    application_id: Optional[int],
    s1: dict,
    s2: dict,
    s3: dict,
    link_confidence: int,
    link_method: str,
    action_taken: str,
    requires_review: bool,
) -> int:
    """Insert an email_events row. Returns the new row id (or 0 on duplicate)."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO email_events (
            application_id, gmail_message_id, gmail_thread_id,
            received_date, sender_email, sender_name, subject, body_snippet,
            signal1_fired, signal2_fired, signal2_tag,
            signal3_fired, signal3_company, signal3_title,
            signal3_intent, signal3_datetime, signal3_confidence,
            link_confidence, link_method, action_taken, requires_review,
            created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            application_id,
            email_data["id"],
            email_data["thread_id"],
            email_data["received_date"],
            email_data["sender_email"],
            email_data["sender_name"],
            email_data["subject"],
            email_data["body_snippet"],
            1 if s1.get("fired") else 0,
            1 if s2.get("fired") else 0,
            s2.get("tag_str"),
            1 if s3.get("fired") else 0,
            s3.get("company"),
            s3.get("title"),
            s3.get("intent"),
            s3.get("datetime_str"),
            s3.get("llm_confidence", 0),
            link_confidence,
            link_method,
            action_taken,
            1 if requires_review else 0,
            _now(),
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0


def _update_application_link(
    conn,
    application_id: int,
    email_data: dict,
    s2: dict,
    s3: dict,
    link_confidence: int,
    link_method: str,
) -> None:
    """Patch signal/link fields on the application record."""
    now = _now()
    fields: dict = {
        "link_confidence": link_confidence,
        "link_method": link_method,
        "updated_at": now,
    }

    # Thread link (set for S1 and S2 — both confirm the thread)
    if link_method in ("signal1", "signal1+signal3", "signal2", "signal2+signal3"):
        fields["gmail_thread_id"] = email_data["thread_id"]
        fields["thread_linked_date"] = now

    if s2.get("fired"):
        fields["tag_matched"] = 1
        fields["tag_match_date"] = now

    if s3.get("fired"):
        fields["llm_extracted_company"] = s3.get("company")
        fields["llm_extracted_title"] = s3.get("title")
        fields["llm_match_confidence"] = s3.get("llm_confidence", 0)

    set_parts = [f"{k} = ?" for k in fields]
    vals = list(fields.values()) + [application_id]
    conn.execute(
        f"UPDATE applications SET {', '.join(set_parts)} WHERE id = ?", vals
    )
    conn.commit()


def _handle_interview(
    conn, application_id: int, s3: dict, event_id: int
) -> None:
    """Advance status to interview_scheduled and record in status_history."""
    now = _now()
    existing = conn.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not existing or existing["status"] == "interview_scheduled":
        return

    old_status = existing["status"]
    update: dict = {"status": "interview_scheduled", "updated_at": now}
    if s3.get("datetime_str"):
        update["interview_datetime"] = s3["datetime_str"]
    if s3.get("intent") in ("interview", "schedule"):
        update["interview_type"] = s3["intent"]

    set_parts = [f"{k} = ?" for k in update]
    conn.execute(
        f"UPDATE applications SET {', '.join(set_parts)} WHERE id = ?",
        list(update.values()) + [application_id],
    )
    conn.execute(
        "INSERT INTO status_history "
        "(application_id, from_status, to_status, triggered_by, "
        "email_event_id, notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            application_id, old_status, "interview_scheduled",
            "email_linker", event_id,
            "Auto-detected from email signal", now,
        ),
    )
    conn.commit()
    log.info(
        "Interview scheduled: app_id=%d  %s → interview_scheduled",
        application_id, old_status,
    )


def _handle_rejection(conn, application_id: int, event_id: int) -> None:
    """Advance status to rejected and record in status_history."""
    now = _now()
    existing = conn.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    terminal = {"rejected", "offer_received", "accepted", "declined", "withdrawn"}
    if not existing or existing["status"] in terminal:
        return

    old_status = existing["status"]
    conn.execute(
        "UPDATE applications SET status = 'rejected', updated_at = ? WHERE id = ?",
        (now, application_id),
    )
    conn.execute(
        "INSERT INTO status_history "
        "(application_id, from_status, to_status, triggered_by, "
        "email_event_id, notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            application_id, old_status, "rejected",
            "email_linker", event_id,
            "Auto-detected rejection from email signal", now,
        ),
    )
    conn.commit()
    log.info(
        "Rejection detected: app_id=%d  %s → rejected", application_id, old_status
    )


# ---------------------------------------------------------------------------
# Core fusion
# ---------------------------------------------------------------------------

def fuse_signals(email_data: dict, s1: dict, s2: dict, s3: dict) -> dict:
    """Compute final confidence, determine application_id, and choose action.

    Pure function — reads config and signal dicts, returns a result dict.
    Does NOT write to the database.

    Returned keys:
      application_id, link_confidence, link_method,
      action ('auto_link'|'link_flag'|'hold'),
      requires_review, is_interview, is_rejection
    """
    cfg = _load_config()
    weights = cfg.get("signal_weights", {})
    thresholds = cfg.get("confidence_thresholds", {})

    w1      = weights.get("signal1_thread_match", 60)
    w2      = weights.get("signal2_tag_match", 70)
    w3_str  = weights.get("signal3_llm_match_strong", 40)
    w3_mod  = weights.get("signal3_llm_match_moderate", 25)
    w3_wk   = weights.get("signal3_llm_match_weak", 10)
    w_bonus = weights.get("agreement_bonus", 20)

    t_auto = thresholds.get("auto_link_and_act", 60)
    t_flag = thresholds.get("link_flag_for_review", 30)

    s1_id = s1.get("application_id")
    s2_id = s2.get("application_id")
    s3_id = s3.get("application_id")
    s3_strength = s3.get("match_strength", "none")

    primary_id: Optional[int] = None
    confidence = 0
    method = "none"
    requires_review = False

    # --- Signal 2 (highest priority: tagged address is deterministic) ---
    if s2.get("fired") and s2_id:
        primary_id = s2_id
        confidence = w2
        method = "signal2"

        if s3.get("fired") and s3_id == primary_id:
            confidence += w_bonus
            method = "signal2+signal3"
        # S2 vs S3 disagreement: trust S2 — no penalty

    # --- Signal 1 (thread match) ---
    elif s1.get("fired") and s1_id:
        primary_id = s1_id
        confidence = w1
        method = "signal1"

        if s3.get("fired") and s3_id:
            if s3_id == primary_id:
                confidence += w_bonus
                method = "signal1+signal3"
            else:
                # Spec: S1 and S3 disagree → confidence 0, flag for review
                log.warning(
                    "Signal1/Signal3 conflict for msg=%s: "
                    "S1→app_id=%d, S3→app_id=%d. Flagging for review.",
                    email_data.get("id"), s1_id, s3_id,
                )
                confidence = 0
                requires_review = True
                method = "s1_s3_conflict"

    # --- Signal 3 alone ---
    elif s3.get("fired") and s3_id:
        primary_id = s3_id
        method = "signal3"
        confidence = {"strong": w3_str, "moderate": w3_mod, "weak": w3_wk}.get(
            s3_strength, 0
        )

    # --- Determine action ---
    if primary_id and confidence >= t_auto:
        action = "auto_link"
    elif primary_id and confidence >= t_flag:
        action = "link_flag"
        requires_review = True
    else:
        action = "hold"
        requires_review = True

    # --- Detect intent ---
    is_interview = _is_interview_email(
        s3.get("intent"),
        email_data.get("subject", ""),
        email_data.get("body_snippet", ""),
        cfg,
    )
    is_rejection = _is_rejection_email(
        s3.get("intent"),
        email_data.get("subject", ""),
        email_data.get("body_snippet", ""),
        cfg,
    )

    return {
        "application_id": primary_id,
        "link_confidence": confidence,
        "link_method": method,
        "action": action,
        "requires_review": requires_review,
        "is_interview": is_interview,
        "is_rejection": is_rejection,
    }


# ---------------------------------------------------------------------------
# Full pipeline entry point
# ---------------------------------------------------------------------------

def process_email(email_data: dict) -> dict:
    """Run all 3 signals, fuse results, persist to DB, and act.

    Called by the poll daemon for each new email.
    Also called by test_inbox.py when running in write mode.

    Returns the fusion result dict augmented with:
      event_id, signal1, signal2, signal3
    """
    from applypilot.email_linker.signal1_thread import check_signal1
    from applypilot.email_linker.signal2_tag import check_signal2
    from applypilot.email_linker.signal3_llm import check_signal3

    s1_id, s1_fired = check_signal1(email_data.get("thread_id", ""))
    s1 = {"application_id": s1_id, "fired": s1_fired}

    s2_id, s2_tag = check_signal2(email_data.get("to_addresses", []))
    s2 = {"application_id": s2_id, "fired": s2_id is not None, "tag_str": s2_tag}

    s3 = check_signal3(
        email_data.get("subject", ""),
        email_data.get("sender_email", ""),
        email_data.get("body_snippet", ""),
    )

    fusion = fuse_signals(email_data, s1, s2, s3)
    conn = _get_conn()

    event_id = _save_email_event(
        conn, email_data,
        fusion["application_id"],
        s1, s2, s3,
        fusion["link_confidence"],
        fusion["link_method"],
        fusion["action"],
        fusion["requires_review"],
    )

    if fusion["action"] in ("auto_link", "link_flag") and fusion["application_id"]:
        _update_application_link(
            conn, fusion["application_id"], email_data,
            s2, s3, fusion["link_confidence"], fusion["link_method"],
        )

        if fusion["is_interview"] and fusion["action"] == "auto_link":
            _handle_interview(conn, fusion["application_id"], s3, event_id)

        if fusion["is_rejection"] and fusion["action"] == "auto_link":
            _handle_rejection(conn, fusion["application_id"], event_id)

    fusion.update({"event_id": event_id, "signal1": s1, "signal2": s2, "signal3": s3})

    log.info(
        "Email %s → app_id=%s  conf=%d  method=%s  action=%s",
        email_data["id"], fusion["application_id"],
        fusion["link_confidence"], fusion["link_method"], fusion["action"],
    )
    return fusion
