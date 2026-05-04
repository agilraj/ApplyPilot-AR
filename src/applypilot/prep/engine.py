"""Interview prep engine — Stage 7 orchestrator.

Entry points:
    generate_prep(application_id, interviewer_name=None) -> str
        Build a full prep package for one application. Returns the output path.
        Safe to call multiple times — regenerates the package each time.

    check_and_trigger_all() -> list[str]
        Scan tracker DB for applications with status='interview_scheduled'
        and no prep_package_path yet. Generate a package for each.
        Returns list of generated file paths.

Trigger wiring:
    Called from fusion.py _handle_interview() when email linker detects an
    interview, and from the 'applypilot prep --id N' CLI command (Session 5).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from applypilot.config import load_profile
from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PREP_CFG_PATH = _PROJECT_ROOT / "config" / "prep.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prep_config() -> dict:
    with open(_PREP_CFG_PATH) as f:
        return json.load(f)


def _get_conn():
    conn = get_tracker_connection()
    try:
        conn.execute("SELECT 1 FROM applications LIMIT 1")
    except Exception:
        conn = init_tracker_db()
    return conn


def _get_application(app_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    return dict(row) if row else None


def _get_jd(job_url: Optional[str]) -> str:
    """Fetch full_description from the applypilot jobs DB by URL."""
    if not job_url:
        return ""
    try:
        from applypilot.database import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT full_description FROM jobs WHERE url = ?", (job_url,)
        ).fetchone()
        if row and row[0]:
            return str(row[0])[:8000]
    except Exception as exc:
        log.warning("Could not fetch JD for job_url=%s: %s", job_url, exc)
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_prep_written(app_id: int, file_path: str) -> None:
    conn = _get_conn()
    now = _now()
    conn.execute(
        "UPDATE applications SET prep_package_path = ?, prep_generated_date = ?, "
        "updated_at = ? WHERE id = ?",
        (file_path, now, now, app_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_prep(
    application_id: int,
    interviewer_name: Optional[str] = None,
) -> str:
    """Generate a full interview prep package for one application.

    Args:
        application_id: Tracker DB application id.
        interviewer_name: Override interviewer name (takes precedence over DB value).

    Returns:
        Absolute path to the generated markdown file.

    Raises:
        ValueError: If application_id is not found in the tracker DB.
    """
    cfg = _load_prep_config()
    sections_cfg = cfg.get("sections", {})

    output_dir = _PROJECT_ROOT / cfg.get("output_dir", "prep_packages")
    output_dir.mkdir(parents=True, exist_ok=True)

    app = _get_application(application_id)
    if not app:
        raise ValueError(f"Application #{application_id} not found in tracker DB.")

    company       = app.get("company_name") or "Unknown Company"
    country       = app.get("job_location_country") or None
    is_remote     = bool(app.get("job_is_remote"))
    currency      = app.get("salary_currency") or "MYR"
    job_url       = app.get("job_url")
    jd_text       = _get_jd(job_url)
    interviewer   = interviewer_name or app.get("interviewer_name") or None

    try:
        profile = load_profile()
    except FileNotFoundError:
        log.warning("profile.json not found — LLM sections will have limited personalisation")
        profile = {}

    today = date.today().isoformat()
    built: dict = {}

    log.info("Generating prep package for app_id=%d (%s)", application_id, company)

    # ── Section 1: Company Brief ─────────────────────────────────────────────
    if sections_cfg.get("company_brief", True):
        log.info("  [1/9] Company brief...")
        from applypilot.prep.company_research import research_company
        location_hint = " ".join(filter(None, [
            app.get("job_location_city"), country
        ]))
        built["company_brief"] = research_company(company, location_hint)

    # ── Section 2+3: Role Analysis + Talking Points ──────────────────────────
    if sections_cfg.get("role_analysis", True) or sections_cfg.get("talking_points", True):
        log.info("  [2+3/9] Role analysis + talking points...")
        from applypilot.prep.role_analysis import analyse_role
        role_data = analyse_role(jd_text, profile)
        built["role_analysis"]  = role_data.get("analysis", {})
        built["talking_points"] = role_data.get("talking_points", [])

    # ── Section 4+5: Predicted Questions + Questions to Ask ──────────────────
    if sections_cfg.get("predicted_questions", True) or sections_cfg.get("questions_to_ask", True):
        log.info("  [4+5/9] Generating questions...")
        from applypilot.prep.questions import generate_questions
        q_data = generate_questions(
            jd_text,
            built.get("company_brief", {}),
            built.get("role_analysis", {}),
            profile,
        )
        built["predicted_questions"] = q_data.get("predicted", {})
        built["questions_to_ask"]    = q_data.get("questions_to_ask", [])

    # ── Section 6: Salary Anchor ─────────────────────────────────────────────
    if sections_cfg.get("salary_anchor", True):
        log.info("  [6/9] Salary anchor...")
        from applypilot.prep.salary_anchor import build_salary_anchor
        built["salary_anchor"] = build_salary_anchor(country, is_remote, currency, cfg)

    # ── Section 7: Interviewer Research ──────────────────────────────────────
    if sections_cfg.get("interviewer_research", True) and interviewer:
        log.info("  [7/9] Interviewer research for '%s'...", interviewer)
        from applypilot.prep.company_research import research_interviewer
        built["interviewer_research"] = research_interviewer(interviewer, company)
    elif sections_cfg.get("interviewer_research", True):
        log.info("  [7/9] Interviewer research skipped (no interviewer_name stored)")

    # ── Section 8: Compensation Intelligence ─────────────────────────────────
    if sections_cfg.get("compensation_intelligence", True):
        log.info("  [8/9] Compensation intelligence...")
        built["compensation_intel"] = {
            "estimated_min":        app.get("salary_estimated_min"),
            "estimated_max":        app.get("salary_estimated_max"),
            "inference_confidence": app.get("salary_inference_confidence"),
            "currency":             currency,
        }

    # ── Section 9: Relocation Intel ──────────────────────────────────────────
    if sections_cfg.get("relocation_intel", True) and app.get("relocation_required"):
        log.info("  [9/9] Relocation intel for '%s'...", country)
        from applypilot.prep.company_research import research_relocation
        built["relocation_intel"] = research_relocation(
            country or "", city=app.get("job_location_city") or ""
        )
    elif sections_cfg.get("relocation_intel", True):
        log.info("  [9/9] Relocation intel skipped (relocation_required=False)")

    # ── Render ────────────────────────────────────────────────────────────────
    log.info("  Rendering markdown...")
    from applypilot.prep.renderer import render
    file_path = render(
        app_id=application_id,
        company=company,
        date_str=today,
        app=app,
        jd_text=jd_text,
        sections=built,
        output_dir=output_dir,
    )

    # ── Update tracker ────────────────────────────────────────────────────────
    _mark_prep_written(application_id, file_path)

    log.info("Prep package complete: %s", file_path)
    print(f"\nPrep package generated: {file_path}")
    return file_path


# ---------------------------------------------------------------------------
# Batch trigger
# ---------------------------------------------------------------------------

def check_and_trigger_all() -> list[str]:
    """Generate prep packages for all interview_scheduled apps that don't have one.

    Returns list of file paths generated in this run.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, company_name FROM applications "
        "WHERE status = 'interview_scheduled' AND "
        "(prep_package_path IS NULL OR prep_package_path = '')"
    ).fetchall()

    if not rows:
        log.info("check_and_trigger_all: no pending prep packages")
        return []

    generated: list[str] = []
    for row in rows:
        app_id = row["id"]
        company = row["company_name"]
        try:
            path = generate_prep(app_id)
            generated.append(path)
        except Exception as exc:
            log.error("Prep generation failed for app_id=%d (%s): %s", app_id, company, exc)

    return generated
