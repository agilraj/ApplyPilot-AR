"""All tracker DB read/write/update functions.

Public API consumed by pipeline stage hooks:
  sync_discovered_from_jobs()    — called at end of discover stage
  sync_scored_from_jobs()        — called at end of score stage
  update_resume_path()           — called at end of tailor stage
  update_cover_letter_path()     — called at end of cover stage
  set_applied()                  — called at end of apply stage

All functions are fail-safe: they log warnings instead of raising, so a
tracker failure never blocks the pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    """Return tracker connection, auto-initialising schema on first use."""
    conn = get_tracker_connection()
    try:
        conn.execute("SELECT 1 FROM applications LIMIT 1")
    except sqlite3.OperationalError:
        conn = init_tracker_db()
    return conn


def _log_status_change(
    conn: sqlite3.Connection,
    application_id: int,
    from_status: Optional[str],
    to_status: Optional[str],
    triggered_by: str,
    notes: Optional[str] = None,
) -> None:
    """Append a row to status_history."""
    conn.execute(
        "INSERT INTO status_history "
        "(application_id, from_status, to_status, triggered_by, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, from_status, to_status, triggered_by, notes, _now()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core upsert
# ---------------------------------------------------------------------------

def upsert_application(
    job_url: str,
    job_title: str,
    company_name: str,
    **fields,
) -> int:
    """Insert or update an application record. Returns the application id.

    On insert: creates a new record with status='discovered' (unless overridden).
    On update: patches the provided fields and logs a status_history entry if
    the status changed.
    """
    conn = _get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT id, status FROM applications WHERE job_url = ?", (job_url,)
    ).fetchone()

    if existing is None:
        fields.setdefault("status", "discovered")
        cols = ["job_url", "job_title", "company_name", "created_at", "updated_at"]
        vals: list = [job_url, job_title, company_name, now, now]

        for k, v in fields.items():
            if v is not None:
                cols.append(k)
                vals.append(v)

        placeholders = ",".join("?" * len(vals))
        cursor = conn.execute(
            f"INSERT INTO applications ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()
        app_id = cursor.lastrowid
        log.debug("Tracker insert: app_id=%d url=%s", app_id, job_url[:60])

        new_status = fields.get("status", "discovered")
        _log_status_change(conn, app_id, None, new_status, "pipeline")
        return app_id

    app_id: int = existing["id"]
    old_status: str = existing["status"]
    new_status = fields.get("status", old_status)

    set_parts = ["updated_at = ?"]
    set_vals: list = [now]
    for k, v in fields.items():
        if v is not None:
            set_parts.append(f"{k} = ?")
            set_vals.append(v)

    set_vals.append(job_url)
    conn.execute(
        f"UPDATE applications SET {', '.join(set_parts)} WHERE job_url = ?",
        set_vals,
    )
    conn.commit()

    if new_status and new_status != old_status:
        _log_status_change(conn, app_id, old_status, new_status, "pipeline")

    log.debug("Tracker update: app_id=%d status=%s url=%s", app_id, new_status, job_url[:60])
    return app_id


# ---------------------------------------------------------------------------
# Pipeline stage hooks
# ---------------------------------------------------------------------------

def sync_discovered_from_jobs() -> int:
    """Sync newly discovered jobs from applypilot.db into the tracker.

    Called at the end of the discover stage. Only inserts jobs that do not
    already have an applications row (by job_url). Returns the count inserted.
    """
    from applypilot.database import get_connection as _jobs_conn

    jobs_conn = _jobs_conn()
    tracker_conn = _get_conn()

    existing_urls: set[str] = {
        row[0]
        for row in tracker_conn.execute(
            "SELECT job_url FROM applications WHERE job_url IS NOT NULL"
        ).fetchall()
    }

    rows = jobs_conn.execute(
        "SELECT url, title, site, location FROM jobs"
    ).fetchall()

    count = 0
    for row in rows:
        url = row["url"]
        if not url or url in existing_urls:
            continue
        try:
            upsert_application(
                job_url=url,
                job_title=row["title"] or "Unknown",
                company_name=row["site"] or "Unknown",
                job_location_raw=row["location"],
                status="discovered",
            )
            existing_urls.add(url)
            count += 1
        except Exception as exc:
            log.warning("Tracker discover sync failed for %s: %s", str(url)[:60], exc)

    log.info("Tracker: synced %d new applications (discover)", count)
    return count


def sync_scored_from_jobs() -> int:
    """Sync scored jobs from applypilot.db into the tracker.

    Called at the end of the score stage. Upserts all jobs that have a
    fit_score, updating the tracker's fit_score and status='scored'.
    Returns the count upserted.
    """
    from applypilot.database import get_connection as _jobs_conn

    jobs_conn = _jobs_conn()

    rows = jobs_conn.execute(
        "SELECT url, title, site, location, fit_score FROM jobs "
        "WHERE fit_score IS NOT NULL"
    ).fetchall()

    count = 0
    for row in rows:
        url = row["url"]
        if not url:
            continue
        try:
            upsert_application(
                job_url=url,
                job_title=row["title"] or "Unknown",
                company_name=row["site"] or "Unknown",
                job_location_raw=row["location"],
                fit_score=float(row["fit_score"]),
                status="scored",
            )
            count += 1
        except Exception as exc:
            log.warning("Tracker score sync failed for %s: %s", str(url)[:60], exc)

    log.info("Tracker: synced %d scored applications", count)
    return count


def update_resume_path(job_url: str, resume_path: str) -> None:
    """Update resume_version_path and advance status to 'tailored'.

    Called at the end of the tailor stage for each successfully tailored job.
    """
    conn = _get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT id, status FROM applications WHERE job_url = ?", (job_url,)
    ).fetchone()

    if existing is None:
        log.warning("Tracker tailor: no application row for %s", job_url[:60])
        return

    app_id: int = existing["id"]
    old_status: str = existing["status"]

    conn.execute(
        "UPDATE applications SET resume_version_path = ?, status = 'tailored', "
        "updated_at = ? WHERE job_url = ?",
        (resume_path, now, job_url),
    )
    conn.commit()

    if old_status != "tailored":
        _log_status_change(conn, app_id, old_status, "tailored", "tailor_stage")


def update_cover_letter_path(job_url: str, cover_letter_path: str) -> None:
    """Update cover_letter_path in the tracker.

    Called at the end of the cover stage for each successfully generated letter.
    Status is not advanced here — tailored is the last formal stage before apply.
    """
    conn = _get_conn()
    now = _now()

    conn.execute(
        "UPDATE applications SET cover_letter_path = ?, updated_at = ? "
        "WHERE job_url = ?",
        (cover_letter_path, now, job_url),
    )
    conn.commit()
    log.debug("Tracker cover: updated cover_letter_path for %s", job_url[:60])


def set_applied(job_url: str, applied_email: Optional[str] = None) -> None:
    """Mark an application as applied with timestamp.

    Called at the end of the apply stage when a job is successfully submitted.
    """
    conn = _get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT id, status FROM applications WHERE job_url = ?", (job_url,)
    ).fetchone()

    if existing is None:
        log.warning("Tracker apply: no application row for %s", job_url[:60])
        return

    app_id: int = existing["id"]
    old_status: str = existing["status"]

    conn.execute(
        "UPDATE applications SET status = 'applied', applied_date = ?, "
        "applied_email = ?, updated_at = ? WHERE job_url = ?",
        (now, applied_email, now, job_url),
    )
    conn.commit()

    _log_status_change(conn, app_id, old_status, "applied", "apply_stage")
    log.info("Tracker apply: marked applied for %s", job_url[:60])


# ---------------------------------------------------------------------------
# Read helpers (used by dashboard / CLI status commands)
# ---------------------------------------------------------------------------

def get_application_by_url(job_url: str) -> Optional[dict]:
    """Fetch a single application by job URL. Returns None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM applications WHERE job_url = ?", (job_url,)
    ).fetchone()
    return dict(row) if row else None


def get_all_applications(status: Optional[str] = None) -> list[dict]:
    """Return all applications, optionally filtered by status, newest first."""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM applications WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM applications ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_status_history(application_id: int) -> list[dict]:
    """Return status history for an application, oldest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM status_history WHERE application_id = ? ORDER BY created_at ASC",
        (application_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_application(job_url: str, **fields) -> None:
    """Patch arbitrary fields on an application by job_url.

    Used by the CLI update command and the email linker / prep engine.
    Logs a status_history entry if 'status' is among the updated fields.
    """
    if not fields:
        return

    conn = _get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT id, status FROM applications WHERE job_url = ?", (job_url,)
    ).fetchone()
    if existing is None:
        log.warning("Tracker update_application: no row for %s", job_url[:60])
        return

    app_id: int = existing["id"]
    old_status: str = existing["status"]
    new_status: Optional[str] = fields.get("status")

    set_parts = ["updated_at = ?"]
    set_vals: list = [now]
    for k, v in fields.items():
        set_parts.append(f"{k} = ?")
        set_vals.append(v)
    set_vals.append(job_url)

    conn.execute(
        f"UPDATE applications SET {', '.join(set_parts)} WHERE job_url = ?",
        set_vals,
    )
    conn.commit()

    if new_status and new_status != old_status:
        _log_status_change(conn, app_id, old_status, new_status, "cli_update")
