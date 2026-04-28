"""Signal 1: Thread ID match.

Looks up the email's gmail_thread_id in applications.gmail_thread_id.
A hit means this email is a reply to a thread we already linked to an application.

Confidence contribution: +60 (from config/gmail.json signal_weights.signal1_thread_match)
"""

from __future__ import annotations

import logging

from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)


def check_signal1(thread_id: str) -> tuple[int | None, bool]:
    """Return (application_id, fired) for the given Gmail thread_id.

    fired=True and application_id is set when the thread_id matches a known
    application record. fired=False otherwise.
    """
    if not thread_id:
        return None, False

    try:
        conn = get_tracker_connection()
        try:
            row = conn.execute(
                "SELECT id FROM applications WHERE gmail_thread_id = ?",
                (thread_id,),
            ).fetchone()
        except Exception:
            conn = init_tracker_db()
            row = conn.execute(
                "SELECT id FROM applications WHERE gmail_thread_id = ?",
                (thread_id,),
            ).fetchone()

        if row:
            app_id: int = row["id"]
            log.debug("Signal1 FIRED: thread_id=%s → app_id=%d", thread_id, app_id)
            return app_id, True

    except Exception as exc:
        log.warning("Signal1 DB error for thread_id=%s: %s", thread_id, exc)

    return None, False
