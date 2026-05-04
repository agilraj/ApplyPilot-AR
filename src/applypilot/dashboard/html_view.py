"""HTML tracker dashboard for applypilot dashboard command.

Generates a self-contained HTML file with:
  - Needs Attention section at top (salary flags, email confidence gaps, missing prep)
  - Kanban board: Applied | Responded | Interview | Offer | Closed
  - Each column grouped by region (SEA / Australia / Europe / Remote / Other)
  - Cards: company, title, location flag emoji, score badge, salary gate pill, days since applied
  - Color coding: green = above target, yellow = borderline/undisclosed, red = below floor
"""

from __future__ import annotations

import logging
import webbrowser
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()

# Kanban column order and status membership
KANBAN_COLUMNS = [
    ("applied",   "Applied",    ["applied"]),
    ("responded", "Responded",  ["responded"]),
    ("interview", "Interview",  ["interview_scheduled", "interviewing"]),
    ("offer",     "Offer",      ["offer_received", "accepted", "declined"]),
    ("closed",    "Closed",     ["rejected", "ghosted", "withdrawn"]),
]

REGION_ORDER = ["SEA", "Australia", "Europe", "Remote", "Other"]

_FLAG_EMOJI: dict[str, str] = {
    "Malaysia": "🇲🇾", "Singapore": "🇸🇬", "Australia": "🇦🇺",
    "UK": "🇬🇧", "United Kingdom": "🇬🇧", "Germany": "🇩🇪",
    "Netherlands": "🇳🇱", "Sweden": "🇸🇪", "Thailand": "🇹🇭",
    "Indonesia": "🇮🇩", "Philippines": "🇵🇭", "Vietnam": "🇻🇳",
    "UAE": "🇦🇪", "France": "🇫🇷", "Switzerland": "🇨🇭",
    "Norway": "🇳🇴", "Denmark": "🇩🇰", "Belgium": "🇧🇪",
    "Austria": "🇦🇹", "Spain": "🇪🇸", "Portugal": "🇵🇹",
    "Poland": "🇵🇱", "Czech Republic": "🇨🇿", "Hungary": "🇭🇺",
    "New Zealand": "🇳🇿",
}

# Maps salary_gate_result → CSS class suffix and display label
_GATE_CSS: dict[str, str] = {
    "pass": "green", "above_target": "green", "at_target": "green",
    "disclosed_above_target": "green", "disclosed_at_target": "green",
    "borderline": "yellow", "flagged": "yellow",
    "undisclosed_proceeding": "yellow", "salary_tbc_override": "yellow",
    "disclosed_above_floor_below_target": "yellow",
    "below_floor": "red", "disclosed_below_floor": "red",
}

_GATE_LABEL: dict[str, str] = {
    "pass": "✓ Pass",
    "above_target": "✓ Above Target",
    "at_target": "✓ At Target",
    "disclosed_above_target": "✓ Above Target",
    "disclosed_at_target": "✓ At Target",
    "borderline": "~ Borderline",
    "flagged": "~ Flagged",
    "undisclosed_proceeding": "~ Undisclosed",
    "salary_tbc_override": "~ TBC at Interview",
    "disclosed_above_floor_below_target": "~ Below Target",
    "below_floor": "✗ Below Floor",
    "disclosed_below_floor": "✗ Below Floor",
}

_ATTENTION_GATES = frozenset({
    "borderline", "flagged", "salary_tbc_override",
    "disclosed_above_floor_below_target",
})

_SEA = {"Malaysia", "Singapore", "Thailand", "Indonesia", "Philippines", "Vietnam", "UAE"}
_EUR = {
    "Germany", "Netherlands", "UK", "United Kingdom", "Sweden", "France",
    "Belgium", "Austria", "Switzerland", "Norway", "Denmark", "Spain",
    "Portugal", "Poland", "Czech Republic", "Hungary",
}
_AUS = {"Australia", "New Zealand"}


def _region(app: dict) -> str:
    country = (app.get("job_location_country") or "").strip()
    tier = (app.get("location_tier") or "").strip()

    if app.get("job_is_remote") or tier == "remote":
        return "Remote"
    if country in _SEA:
        return "SEA"
    if country in _AUS:
        return "Australia"
    if country in _EUR:
        return "Europe"

    raw = (app.get("job_location_raw") or "").lower()
    if "remote" in raw:
        return "Remote"
    for c in _SEA:
        if c.lower() in raw:
            return "SEA"
    for c in _AUS:
        if c.lower() in raw:
            return "Australia"
    for c in _EUR:
        if c.lower() in raw:
            return "Europe"

    return "Other"


def _kanban_col(app: dict) -> Optional[str]:
    status = app.get("status") or ""
    for col_key, _, statuses in KANBAN_COLUMNS:
        if status in statuses:
            return col_key
    return None


def _days_since(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).days
        if days == 0:
            return "today"
        if days == 1:
            return "1d ago"
        return f"{days}d ago"
    except Exception:
        return "—"


def _location_flag(app: dict) -> str:
    if app.get("job_is_remote"):
        return "🌐"
    country = (app.get("job_location_country") or "").strip()
    return _FLAG_EMOJI.get(country, "📍")


def _get_review_app_ids() -> set[int]:
    try:
        from applypilot.tracker.db import get_tracker_connection
        conn = get_tracker_connection()
        rows = conn.execute(
            "SELECT DISTINCT application_id FROM email_events "
            "WHERE requires_review = 1 AND application_id IS NOT NULL"
        ).fetchall()
        return {int(r[0]) for r in rows}
    except Exception:
        return set()


def _needs_attention(app: dict, review_ids: set[int]) -> bool:
    if app.get("id") in review_ids:
        return True
    gate = app.get("salary_gate_result") or ""
    if gate in _ATTENTION_GATES:
        return True
    conf = app.get("link_confidence")
    if conf is not None and 30 <= int(conf) < 60:
        return True
    if app.get("status") == "interview_scheduled" and not app.get("prep_package_path"):
        return True
    return False


def _flag_reasons(app: dict, review_ids: set[int]) -> list[str]:
    reasons = []
    gate = app.get("salary_gate_result") or ""
    if gate in _ATTENTION_GATES:
        reasons.append(f"Salary: {_GATE_LABEL.get(gate, gate)}")
    conf = app.get("link_confidence")
    if conf is not None and 30 <= int(conf) < 60:
        reasons.append(f"Email confidence: {conf}%")
    if app.get("id") in review_ids:
        reasons.append("Email review needed")
    if app.get("status") == "interview_scheduled" and not app.get("prep_package_path"):
        reasons.append("Prep package not generated")
    return reasons


def _score_badge(score: Optional[float]) -> str:
    if score is None:
        return '<span class="score-badge score-none" title="Not scored">—</span>'
    cls = "score-green" if score >= 8 else ("score-yellow" if score >= 6 else "score-red")
    return f'<span class="score-badge {cls}" title="Fit score: {score:.1f}">{score:.1f}</span>'


def _gate_pill(gate: Optional[str]) -> str:
    if not gate:
        return '<span class="gate-pill gate-none">—</span>'
    css = _GATE_CSS.get(gate, "none")
    label = _GATE_LABEL.get(gate, gate)
    return f'<span class="gate-pill gate-{css}">{escape(label)}</span>'


def _breakdown_str(app: dict) -> str:
    parts = []
    for key, label in [
        ("resume_score", "R"), ("role_score", "J"),
        ("location_score", "L"), ("salary_score", "S"),
    ]:
        v = app.get(key)
        if v is not None:
            parts.append(f"{label}:{float(v):.0f}")
    return " | ".join(parts)


def _make_card(app: dict, review_ids: set[int], is_flagged: bool = False) -> str:
    app_id = app.get("id", "?")
    company = escape((app.get("company_name") or "Unknown")[:32])
    title_raw = (app.get("job_title") or "Unknown")[:44]
    flag_emoji = _location_flag(app)
    location = escape(
        (app.get("job_location_raw") or app.get("job_location_city") or "—")[:32]
    )
    score_html = _score_badge(
        float(app["fit_score"]) if app.get("fit_score") is not None else None
    )
    gate_html = _gate_pill(app.get("salary_gate_result"))
    days = escape(_days_since(app.get("applied_date")))
    breakdown = escape(_breakdown_str(app))
    tier = escape(app.get("location_tier") or "—")

    gate = app.get("salary_gate_result") or ""
    gate_css = _GATE_CSS.get(gate, "none")
    flagged_class = " flagged" if is_flagged else ""

    job_url = (app.get("job_url") or "").strip()
    if job_url:
        title_html = f'<a href="{escape(job_url)}" class="card-title" target="_blank">{escape(title_raw)}</a>'
    else:
        title_html = f'<span class="card-title">{escape(title_raw)}</span>'

    flag_icon_html = ""
    if is_flagged:
        reasons = _flag_reasons(app, review_ids)
        reasons_str = escape("; ".join(reasons))
        flag_icon_html = f'<span class="flag-icon" title="{reasons_str}">⚠️</span>'

    breakdown_html = (
        f'<div class="card-breakdown">{breakdown}</div>' if breakdown else ""
    )

    return f"""<div class="app-card border-gate-{gate_css}{flagged_class}" data-id="{app_id}">
  <div class="card-top">{flag_icon_html}<span class="card-id">#{app_id}</span>{score_html}</div>
  <div class="card-company">{company}</div>
  {title_html}
  <div class="card-location">{flag_emoji} {location}</div>
  <div class="card-tier">Tier: {tier}</div>
  <div class="card-meta">{gate_html}<span class="days-badge">{days}</span></div>
  {breakdown_html}
</div>"""


def _attention_section(apps: list[dict], review_ids: set[int]) -> str:
    if not apps:
        return ""
    cards = "".join(_make_card(a, review_ids, is_flagged=True) for a in apps)
    return f"""<section class="needs-attention-section">
  <div class="attention-header">
    <h2>⚠️ Needs Attention <span class="attn-count">{len(apps)}</span></h2>
    <p class="section-desc">Review these before proceeding — salary flags, uncertain email matches, or missing prep packages.</p>
  </div>
  <div class="attn-grid">{cards}</div>
</section>"""


def _kanban_column_html(col_key: str, col_label: str, apps: list[dict],
                         review_ids: set[int]) -> str:
    count = len(apps)
    if not apps:
        body = '<p class="empty-col">No applications</p>'
    else:
        by_region: dict[str, list] = {}
        for a in apps:
            by_region.setdefault(_region(a), []).append(a)

        parts = []
        for region in REGION_ORDER:
            rlist = by_region.get(region)
            if not rlist:
                continue
            rlist.sort(key=lambda a: -(float(a.get("fit_score") or 0)))
            cards = "".join(_make_card(a, review_ids) for a in rlist)
            parts.append(
                f'<div class="region-group">'
                f'<div class="region-label">{escape(region)} '
                f'<span class="region-count">{len(rlist)}</span></div>'
                f'{cards}</div>'
            )
        body = "".join(parts) if parts else '<p class="empty-col">No applications</p>'

    return f"""<div class="kanban-col" id="col-{col_key}">
  <div class="col-header">
    <h3>{escape(col_label)}</h3>
    <span class="col-count">{count}</span>
  </div>
  <div class="col-body">{body}</div>
</div>"""


_CSS = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 1.5rem; min-height: 100vh;
  }
  h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: 0.25rem; }
  .subtitle { color: #64748b; font-size: 0.82rem; margin-bottom: 1.5rem; }

  /* Stats bar */
  .stats-bar { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 2rem; }
  .stat-pill {
    background: #1e293b; border-radius: 10px; padding: 0.55rem 1rem;
    display: flex; flex-direction: column; min-width: 80px;
  }
  .stat-pill .num { font-size: 1.4rem; font-weight: 700; }
  .stat-pill .lbl { font-size: 0.68rem; color: #64748b; margin-top: 0.1rem; }
  .stat-pill.blue .num   { color: #60a5fa; }
  .stat-pill.green .num  { color: #10b981; }
  .stat-pill.yellow .num { color: #f59e0b; }
  .stat-pill.purple .num { color: #a78bfa; }
  .stat-pill.red .num    { color: #f87171; }

  /* Needs Attention */
  .needs-attention-section {
    background: #1a0f0f; border: 1px solid #ef444455;
    border-radius: 12px; padding: 1.25rem; margin-bottom: 2rem;
  }
  .attention-header h2 { font-size: 1.05rem; color: #f87171; margin-bottom: 0.3rem; }
  .attn-count {
    background: #ef4444; color: white; font-size: 0.7rem;
    padding: 0.1rem 0.45rem; border-radius: 10px; font-weight: 700;
  }
  .section-desc { color: #94a3b8; font-size: 0.78rem; margin-bottom: 1rem; }
  .attn-grid { display: flex; gap: 0.75rem; flex-wrap: wrap; }

  /* Kanban board */
  .kanban-board {
    display: flex; gap: 1rem; overflow-x: auto; padding-bottom: 1rem;
    align-items: flex-start;
  }
  .kanban-col {
    flex: 0 0 255px; min-width: 255px; background: #1e293b;
    border-radius: 12px; display: flex; flex-direction: column;
  }
  .col-header {
    padding: 0.85rem 1rem 0.7rem; border-bottom: 1px solid #334155;
    display: flex; justify-content: space-between; align-items: center;
    position: sticky; top: 0; background: #1e293b; border-radius: 12px 12px 0 0; z-index: 1;
  }
  .col-header h3 {
    font-size: 0.8rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.07em; color: #64748b;
  }
  #col-applied   .col-header h3 { color: #60a5fa; }
  #col-responded .col-header h3 { color: #a78bfa; }
  #col-interview .col-header h3 { color: #10b981; }
  #col-offer     .col-header h3 { color: #f59e0b; }
  #col-closed    .col-header h3 { color: #475569; }
  .col-count {
    background: #334155; color: #94a3b8; font-size: 0.72rem;
    padding: 0.12rem 0.5rem; border-radius: 10px; font-weight: 600;
  }
  .col-body {
    padding: 0.75rem; overflow-y: auto;
    max-height: calc(100vh - 260px);
  }
  .empty-col {
    color: #334155; font-size: 0.78rem; text-align: center;
    padding: 2rem 0; font-style: italic;
  }

  /* Region groups */
  .region-group { margin-bottom: 0.85rem; }
  .region-label {
    font-size: 0.65rem; font-weight: 700; color: #334155;
    text-transform: uppercase; letter-spacing: 0.08em;
    margin-bottom: 0.4rem; display: flex; align-items: center; gap: 0.4rem;
  }
  .region-count {
    background: #1e293b; color: #475569; font-size: 0.62rem;
    padding: 0.08rem 0.35rem; border-radius: 8px;
  }

  /* App cards */
  .app-card {
    background: #0f172a; border-radius: 8px; padding: 0.7rem;
    margin-bottom: 0.5rem; border-left: 3px solid #334155;
    transition: transform 0.1s, box-shadow 0.1s; position: relative;
  }
  .app-card:hover { transform: translateY(-1px); box-shadow: 0 3px 10px #00000055; }
  .app-card.border-gate-green  { border-left-color: #10b981; }
  .app-card.border-gate-yellow { border-left-color: #f59e0b; }
  .app-card.border-gate-red    { border-left-color: #ef4444; }
  .app-card.border-gate-none   { border-left-color: #334155; }
  .app-card.flagged {
    border-left-color: #ef4444 !important;
    background: #1a0d0d;
  }

  .card-top {
    display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem;
  }
  .card-id { font-size: 0.62rem; color: #334155; }
  .flag-icon {
    position: absolute; top: 0.45rem; right: 0.45rem;
    font-size: 0.85rem; cursor: help;
  }
  .card-company {
    font-size: 0.72rem; font-weight: 700; color: #94a3b8; margin-bottom: 0.2rem;
  }
  .card-title {
    font-size: 0.8rem; font-weight: 600; color: #e2e8f0; line-height: 1.3;
    margin-bottom: 0.35rem; display: block; text-decoration: none;
  }
  a.card-title:hover { color: #60a5fa; text-decoration: underline; }
  .card-location { font-size: 0.7rem; color: #475569; margin-bottom: 0.2rem; }
  .card-tier     { font-size: 0.65rem; color: #334155; margin-bottom: 0.35rem; }
  .card-meta {
    display: flex; gap: 0.35rem; align-items: center; flex-wrap: wrap;
  }
  .card-breakdown {
    font-size: 0.62rem; color: #334155; margin-top: 0.3rem;
    font-family: 'Courier New', monospace; letter-spacing: 0.03em;
  }

  /* Score badge */
  .score-badge {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 1.45rem; height: 1.45rem; border-radius: 5px;
    font-size: 0.7rem; font-weight: 700; color: #0f172a;
  }
  .score-green  { background: #10b981; }
  .score-yellow { background: #f59e0b; }
  .score-red    { background: #ef4444; }
  .score-none   { background: #334155; color: #64748b; }

  /* Gate pill */
  .gate-pill {
    font-size: 0.62rem; padding: 0.1rem 0.4rem;
    border-radius: 4px; font-weight: 600;
  }
  .gate-green  { background: #064e3b; color: #6ee7b7; }
  .gate-yellow { background: #451a03; color: #fcd34d; }
  .gate-red    { background: #450a0a; color: #fca5a5; }
  .gate-none   { background: #1e293b; color: #475569; }

  /* Days badge */
  .days-badge {
    font-size: 0.62rem; color: #475569; background: #1e293b;
    padding: 0.1rem 0.4rem; border-radius: 4px;
  }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: #1e293b; }
  ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }

  @media (max-width: 900px) {
    .kanban-board { flex-direction: column; }
    .kanban-col { min-width: unset; flex: unset; width: 100%; }
    .col-body { max-height: 400px; }
  }
"""


def generate_tracker_dashboard(output_path: str | None = None) -> str:
    """Generate a self-contained HTML tracker dashboard.

    Writes the file to APP_DIR/tracker_dashboard.html by default.
    Returns the absolute path to the generated file.
    """
    from applypilot.config import APP_DIR

    out = Path(output_path) if output_path else APP_DIR / "tracker_dashboard.html"

    try:
        from applypilot.tracker.queries import get_all_applications
        apps = get_all_applications()
    except Exception as exc:
        log.warning("Could not read tracker DB: %s", exc)
        apps = []

    review_ids = _get_review_app_ids()

    # Identify flagged applications
    attention = [a for a in apps if _needs_attention(a, review_ids)]

    # Build kanban: all apps go into their column (flagged ones also appear in Needs Attention)
    col_map: dict[str, list] = {k: [] for k, _, _ in KANBAN_COLUMNS}
    pipeline_count = 0
    for a in apps:
        col = _kanban_col(a)
        if col:
            col_map[col].append(a)
        else:
            pipeline_count += 1

    # Stats
    total = len(apps)
    applied = sum(len(col_map[k]) for k, _, _ in KANBAN_COLUMNS)
    responded = len(col_map.get("responded", [])) + len(col_map.get("interview", [])) + len(col_map.get("offer", []))
    interviewing = len(col_map.get("interview", []))
    offers = len(col_map.get("offer", []))

    resp_rate = f"{responded / applied * 100:.0f}%" if applied else "—"
    intv_rate = f"{interviewing / applied * 100:.0f}%" if applied else "—"

    # Build HTML sections
    attn_html = _attention_section(attention, review_ids)

    kanban_html = "".join(
        _kanban_column_html(col_key, col_label, col_map[col_key], review_ids)
        for col_key, col_label, _ in KANBAN_COLUMNS
    )

    pipeline_pill = (
        f'<div class="stat-pill"><span class="num">{pipeline_count}</span>'
        f'<span class="lbl">In Pipeline</span></div>'
        if pipeline_count else ""
    )
    attn_pill = (
        f'<div class="stat-pill red"><span class="num">{len(attention)}</span>'
        f'<span class="lbl">Needs Attention</span></div>'
        if attention else ""
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ApplyPilot Tracker Dashboard</title>
<style>{_CSS}</style>
</head>
<body>

<h1>ApplyPilot Tracker Dashboard</h1>
<p class="subtitle">Generated {now_str} &nbsp;&middot;&nbsp; {total} tracked applications</p>

<div class="stats-bar">
  <div class="stat-pill blue"><span class="num">{total}</span><span class="lbl">Total Tracked</span></div>
  <div class="stat-pill green"><span class="num">{applied}</span><span class="lbl">Applied</span></div>
  <div class="stat-pill purple"><span class="num">{responded}</span><span class="lbl">Responded</span></div>
  <div class="stat-pill green"><span class="num">{interviewing}</span><span class="lbl">Interviewing</span></div>
  <div class="stat-pill yellow"><span class="num">{offers}</span><span class="lbl">Offers</span></div>
  <div class="stat-pill blue"><span class="num">{resp_rate}</span><span class="lbl">Response Rate</span></div>
  <div class="stat-pill green"><span class="num">{intv_rate}</span><span class="lbl">Interview Rate</span></div>
  {attn_pill}
  {pipeline_pill}
</div>

{attn_html}

<div class="kanban-board">
  {kanban_html}
</div>

</body>
</html>"""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    abs_path = str(out.resolve())
    console.print(f"[green]Tracker dashboard written to {abs_path}[/green]")
    return abs_path


def open_tracker_dashboard(output_path: str | None = None) -> None:
    """Generate the tracker dashboard and open it in the default browser."""
    path = generate_tracker_dashboard(output_path)
    console.print("[dim]Opening in browser...[/dim]")
    webbrowser.open(f"file:///{path}")
