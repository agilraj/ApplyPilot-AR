"""Tracker database: SQLite connection, schema init, and migrations.

tracker.db lives at the project root (one level above src/).
All three tables — applications, email_events, status_history — are created
here. Schema is taken verbatim from SPEC.md Section 8.
"""

import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Project root: 4 levels up from src/applypilot/tracker/db.py
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
TRACKER_DB_PATH = _PROJECT_ROOT / "tracker.db"

_local = threading.local()


def get_tracker_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Return a thread-local cached SQLite connection to tracker.db.

    WAL mode is enabled so the tracker DB can be read while the pipeline
    is writing without blocking.
    """
    path = str(db_path or TRACKER_DB_PATH)

    if not hasattr(_local, "tracker_conns"):
        _local.tracker_conns = {}

    conn = _local.tracker_conns.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            pass

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.tracker_conns[path] = conn
    return conn


def close_tracker_connection(db_path: Path | str | None = None) -> None:
    """Close the cached tracker connection for the current thread."""
    path = str(db_path or TRACKER_DB_PATH)
    if hasattr(_local, "tracker_conns"):
        conn = _local.tracker_conns.pop(path, None)
        if conn is not None:
            conn.close()


def init_tracker_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create tracker tables. Idempotent — safe to call on every startup.

    Creates three tables:
      - applications   : one row per job application, full lifecycle
      - email_events   : every inbound email processed by the linker
      - status_history : audit trail of every status transition
    """
    path = db_path or TRACKER_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_tracker_connection(path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS applications (
          id                        INTEGER PRIMARY KEY AUTOINCREMENT,
          job_title                 TEXT NOT NULL,
          company_name              TEXT NOT NULL,
          company_domain            TEXT,
          company_type              TEXT,
          job_url                   TEXT UNIQUE,
          job_board                 TEXT,
          fit_score                 REAL,
          resume_score              REAL,
          role_score                REAL,
          location_score            REAL,
          salary_score              REAL,
          resume_version_path       TEXT,
          cover_letter_path         TEXT,
          applied_email             TEXT,
          applied_date              DATETIME,
          status                    TEXT DEFAULT 'discovered',

          job_location_raw          TEXT,
          job_location_city         TEXT,
          job_location_country      TEXT,
          job_is_remote             BOOLEAN DEFAULT FALSE,
          job_is_hybrid             BOOLEAN DEFAULT FALSE,
          hybrid_days_mentioned     INTEGER,
          location_tier             TEXT,
          location_score_value      INTEGER,
          relocation_required       BOOLEAN DEFAULT FALSE,
          relocation_flag           TEXT,

          salary_disclosed          BOOLEAN DEFAULT FALSE,
          salary_currency           TEXT,
          salary_min_posted         INTEGER,
          salary_max_posted         INTEGER,
          salary_floor_applied      INTEGER,
          salary_target_applied     INTEGER,
          salary_gate_result        TEXT,
          salary_inference_confidence INTEGER,
          salary_estimated_min      INTEGER,
          salary_estimated_max      INTEGER,
          salary_form_submitted     INTEGER,
          ppp_rate_used             REAL,

          sponsorship_required      BOOLEAN DEFAULT FALSE,
          sponsorship_confirmed     BOOLEAN,

          gmail_thread_id           TEXT,
          thread_linked_date        DATETIME,
          tag_matched               BOOLEAN DEFAULT FALSE,
          tag_match_date            DATETIME,
          llm_extracted_company     TEXT,
          llm_extracted_title       TEXT,
          llm_match_confidence      INTEGER,
          link_confidence           INTEGER,
          link_method               TEXT,

          interview_datetime        DATETIME,
          interview_type            TEXT,
          interviewer_name          TEXT,
          interviewer_email         TEXT,
          interview_platform        TEXT,
          prep_package_path         TEXT,
          prep_generated_date       DATETIME,

          outcome                   TEXT,
          outcome_date              DATETIME,
          offer_amount              INTEGER,
          offer_currency            TEXT,
          notes                     TEXT,

          created_at                DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at                DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_events (
          id                    INTEGER PRIMARY KEY AUTOINCREMENT,
          application_id        INTEGER REFERENCES applications(id),
          gmail_message_id      TEXT UNIQUE,
          gmail_thread_id       TEXT,
          received_date         DATETIME,
          sender_email          TEXT,
          sender_name           TEXT,
          subject               TEXT,
          body_snippet          TEXT,

          signal1_fired         BOOLEAN DEFAULT FALSE,
          signal2_fired         BOOLEAN DEFAULT FALSE,
          signal2_tag           TEXT,
          signal3_fired         BOOLEAN DEFAULT FALSE,
          signal3_company       TEXT,
          signal3_title         TEXT,
          signal3_intent        TEXT,
          signal3_datetime      TEXT,
          signal3_confidence    INTEGER,

          link_confidence       INTEGER,
          link_method           TEXT,
          action_taken          TEXT,
          requires_review       BOOLEAN DEFAULT FALSE,

          created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS status_history (
          id                    INTEGER PRIMARY KEY AUTOINCREMENT,
          application_id        INTEGER REFERENCES applications(id),
          from_status           TEXT,
          to_status             TEXT,
          triggered_by          TEXT,
          email_event_id        INTEGER REFERENCES email_events(id),
          notes                 TEXT,
          created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    log.debug("Tracker DB initialized at %s", path)
    return conn
