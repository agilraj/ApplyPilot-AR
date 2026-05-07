"""HTML tracker dashboard for applypilot dashboard command.

Generates a self-contained HTML file using the advanced navy corporate design.
Data is pulled live from tracker.db via queries.py.
"""

from __future__ import annotations

import json
import logging
import webbrowser
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Optional

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()

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

_GATE_COLOR: dict[str, str] = {
    "pass": "#00d4aa", "above_target": "#00d4aa", "at_target": "#00d4aa",
    "disclosed_above_target": "#00d4aa", "disclosed_at_target": "#00d4aa",
    "borderline": "#f5a623", "flagged": "#f5a623",
    "undisclosed_proceeding": "#f5a623", "salary_tbc_override": "#f5a623",
    "disclosed_above_floor_below_target": "#f5a623",
    "below_floor": "#e74c6b", "disclosed_below_floor": "#e74c6b",
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

_COUNTRY_COORDS: dict[str, tuple[list[float], str]] = {
    "Malaysia":       ([101.9758,  4.2105], "#3a8eff"),
    "Singapore":      ([103.8198,  1.3521], "#00d4aa"),
    "Australia":      ([133.7751, -25.2744], "#f5a623"),
    "New Zealand":    ([174.8860, -40.9006], "#f5a623"),
    "Germany":        ([ 10.4515, 51.1657], "#e74c6b"),
    "United Kingdom": ([ -3.4359, 55.3781], "#e74c6b"),
    "UK":             ([ -3.4359, 55.3781], "#e74c6b"),
    "Netherlands":    ([  5.2913, 52.1326], "#e74c6b"),
    "Sweden":         ([ 15.6000, 58.6000], "#e74c6b"),
    "France":         ([  2.2137, 46.2276], "#e74c6b"),
    "Belgium":        ([  4.4699, 50.5039], "#e74c6b"),
    "Austria":        ([ 14.5501, 47.5162], "#e74c6b"),
    "Switzerland":    ([  8.2275, 46.8182], "#e74c6b"),
    "Norway":         ([  8.4689, 60.4720], "#e74c6b"),
    "Denmark":        ([  9.5018, 56.2639], "#e74c6b"),
    "Spain":          ([ -3.7038, 40.4168], "#e74c6b"),
    "Portugal":       ([ -8.2245, 39.3999], "#e74c6b"),
    "Poland":         ([ 19.1451, 51.9194], "#e74c6b"),
    "Czech Republic": ([ 15.4729, 49.8175], "#e74c6b"),
    "Hungary":        ([ 19.5033, 47.1625], "#e74c6b"),
    "Thailand":       ([100.9925, 15.8700], "#8a6fd8"),
    "Indonesia":      ([113.9213, -0.7893], "#8a6fd8"),
    "Philippines":    ([121.7740, 12.8797], "#8a6fd8"),
    "Vietnam":        ([108.2772, 14.0583], "#8a6fd8"),
    "UAE":            ([ 53.8478, 23.4241], "#8a6fd8"),
}

_STATUS_DOT_COLOR: dict[str, str] = {
    "interview_scheduled": "#00d4aa", "interviewing": "#00d4aa",
    "offer_received": "#f5a623", "accepted": "#f5a623", "declined": "#f5a623",
    "applied": "#3a8eff", "responded": "#8a6fd8",
    "rejected": "#6878a0", "ghosted": "#6878a0", "withdrawn": "#6878a0",
    "tailored": "#3a8eff", "scored": "#3a8eff", "discovered": "#6878a0",
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

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


def _region_code(app: dict) -> str:
    r = _region(app)
    if r == "SEA":
        country = (app.get("job_location_country") or "").strip()
        return "SG" if country == "Singapore" else "MY"
    if r == "Australia":
        return "AU"
    if r == "Europe":
        return "EU"
    if r == "Remote":
        return "Remote"
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
            return "1d"
        return f"{days}d"
    except Exception:
        return "—"


def _location_display(app: dict) -> str:
    country = (app.get("job_location_country") or "").strip()
    flag = _FLAG_EMOJI.get(country, ("🌐" if app.get("job_is_remote") else "📍"))
    city = (app.get("job_location_city") or app.get("job_location_raw") or country or "—")[:20]
    return f"{flag} {escape(city)}"


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


def _get_recent_activity(limit: int = 5) -> list[dict]:
    try:
        from applypilot.tracker.db import get_tracker_connection
        conn = get_tracker_connection()
        rows = conn.execute(
            "SELECT sh.to_status, sh.triggered_by, sh.created_at, "
            "a.company_name, a.job_title "
            "FROM status_history sh "
            "LEFT JOIN applications a ON a.id = sh.application_id "
            "ORDER BY sh.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Chart data builders
# ---------------------------------------------------------------------------

def _score_distribution(apps: list[dict]) -> dict[str, list[int]]:
    """Return {region_code: [count_per_band]} for 5 score bands."""
    buckets: dict[str, list[int]] = {k: [0, 0, 0, 0, 0] for k in ("MY", "SG", "AU", "EU")}
    for app in apps:
        score = app.get("fit_score")
        if score is None:
            continue
        code = _region_code(app)
        if code not in buckets:
            continue
        band = min(int(float(score) - 1) // 2, 4)
        buckets[code][band] += 1
    return buckets


def _timeline_data(apps: list[dict]) -> tuple[list[str], list[int]]:
    """Return (labels, counts) for the last 14 days based on applied_date."""
    today = date.today()
    day_index = {today - timedelta(days=i): i for i in range(14)}
    counts = [0] * 14
    for app in apps:
        raw = app.get("applied_date") or app.get("created_at")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            d = dt.date()
            if d in day_index:
                counts[day_index[d]] += 1
        except Exception:
            pass
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    labels = [d.strftime("%d %b") for d in days]
    values = list(reversed(counts))
    return labels, values


def _gate_counts(apps: list[dict]) -> list[int]:
    """Return [above_target, above_floor, borderline, undisclosed] counts."""
    above = sum(
        1 for a in apps
        if a.get("salary_gate_result") in {"above_target", "at_target", "pass",
                                            "disclosed_above_target", "disclosed_at_target"}
    )
    floor = sum(
        1 for a in apps
        if a.get("salary_gate_result") == "disclosed_above_floor_below_target"
    )
    borderline = sum(
        1 for a in apps
        if a.get("salary_gate_result") in {"borderline", "flagged", "salary_tbc_override",
                                            "below_floor", "disclosed_below_floor"}
    )
    undisclosed = sum(
        1 for a in apps
        if a.get("salary_gate_result") == "undisclosed_proceeding"
    )
    return [above, floor, borderline, undisclosed]


def _map_dots_js(apps: list[dict]) -> str:
    """Return JS variable declarations for targets and coords based on DB data."""
    country_counts: dict[str, int] = {}
    for app in apps:
        c = (app.get("job_location_country") or "").strip()
        if c and c in _COUNTRY_COORDS:
            country_counts[c] = country_counts.get(c, 0) + 1

    if not country_counts:
        country_counts = {c: 0 for c in _COUNTRY_COORDS if c not in {"UK"}}

    max_count = max(country_counts.values()) if country_counts else 1
    targets = {}
    coords = {}
    for country, count in country_counts.items():
        if country not in _COUNTRY_COORDS:
            continue
        lonlat, color = _COUNTRY_COORDS[country]
        r = 3 + round(4 * (count / max(max_count, 1)))
        targets[country] = {"color": color, "r": r}
        coords[country] = lonlat

    return (
        f"const targets={json.dumps(targets, separators=(',', ':'))};\n"
        f"const coords={json.dumps(coords, separators=(',', ':'))};"
    )


# ---------------------------------------------------------------------------
# HTML fragment builders
# ---------------------------------------------------------------------------

def _kcard(app: dict) -> str:
    company = escape((app.get("company_name") or "Unknown")[:28])
    title = escape((app.get("job_title") or "Unknown")[:36])
    score = app.get("fit_score")
    score_str = f"{float(score):.1f}" if score is not None else "—"
    loc = _location_display(app)
    days = _days_since(app.get("applied_date"))
    gate = app.get("salary_gate_result") or ""
    color = _GATE_COLOR.get(gate, "#3a8eff")
    rc = _region_code(app)
    job_url = (app.get("job_url") or "").strip()
    title_html = (
        f'<a href="{escape(job_url)}" target="_blank" class="kcard-title">{title}</a>'
        if job_url else f'<div class="kcard-title">{title}</div>'
    )
    return (
        f'<div class="kcard" style="--salary-color:{color}" data-region="{rc}">'
        f'<div class="kcard-co">{company}</div>'
        f'{title_html}'
        f'<div class="kcard-foot">'
        f'<span class="kcard-score">{score_str}</span>'
        f'<span class="kcard-loc">{loc}</span>'
        f'<span class="kcard-days">{days}</span>'
        f'</div></div>'
    )


def _kanban_col_html(col_key: str, col_label: str, apps: list[dict],
                     color: str) -> str:
    count = len(apps)
    apps_sorted = sorted(apps, key=lambda a: -(float(a.get("fit_score") or 0)))
    if apps_sorted:
        body = "".join(_kcard(a) for a in apps_sorted)
    else:
        empty_msgs = {
            "applied":   "Run discovery<br>to populate",
            "responded": "Gmail watch<br>monitoring",
            "interview": "Auto-detected<br>via Signal 3",
            "offer":     "—",
            "closed":    "—",
        }
        body = f'<div class="empty-col">{empty_msgs.get(col_key, "—")}</div>'

    return (
        f'<div class="kol">'
        f'<div class="kol-head">'
        f'<span class="kol-label" style="color:{color}">{escape(col_label)}</span>'
        f'<span class="kol-count">{count}</span>'
        f'</div>'
        f'{body}'
        f'</div>'
    )


def _attention_items_html(apps: list[dict], review_ids: set[int]) -> str:
    flagged = [a for a in apps if _needs_attention(a, review_ids)]
    if not flagged:
        return (
            '<div class="attn-item">'
            '<div class="attn-bullet"></div>'
            '<span>No items require attention — pipeline is clean</span>'
            '</div>'
        )
    parts = []
    for a in flagged[:6]:
        company = escape((a.get("company_name") or "Unknown")[:28])
        reasons = "; ".join(_flag_reasons(a, review_ids))
        parts.append(
            f'<div class="attn-item">'
            f'<div class="attn-bullet"></div>'
            f'<span><strong style="color:var(--text1)">{company}</strong>'
            f'{(" — " + escape(reasons)) if reasons else ""}</span>'
            f'</div>'
        )
    return "".join(parts)


def _activity_feed_html(events: list[dict]) -> str:
    if not events:
        return (
            '<div class="activity-item">'
            '<div class="act-dot" style="background:var(--text3)"></div>'
            '<div><div class="act-text">No activity recorded yet</div></div>'
            '</div>'
        )
    parts = []
    for ev in events:
        status = ev.get("to_status") or "—"
        company = escape((ev.get("company_name") or "System")[:28])
        title = escape((ev.get("job_title") or "")[:36])
        triggered = escape(ev.get("triggered_by") or "pipeline")
        dot_color = _STATUS_DOT_COLOR.get(status, "#6878a0")
        raw_ts = ev.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            ts_str = dt.strftime("%d %b · %H:%M")
        except Exception:
            ts_str = raw_ts[:16]
        detail = f"{escape(status.replace('_', ' '))} via {triggered}"
        parts.append(
            f'<div class="activity-item">'
            f'<div class="act-dot" style="background:{dot_color}"></div>'
            f'<div>'
            f'<div class="act-text"><span class="act-co">{company}</span>'
            f'{(" — " + title) if title else ""} · {detail}</div>'
            f'<div class="act-time">{ts_str}</div>'
            f'</div></div>'
        )
    return "".join(parts)


def _salary_floors_html() -> str:
    try:
        from applypilot.salary.config_loader import load_salary_config
        cfg = load_salary_config()
        regions = cfg.get("regions", {})
        display = [
            ("MY", "Malaysia"), ("SG", "Singapore"),
            ("AU", "Australia"), ("EU", "Europe"),
        ]
        rows = []
        for i, (code, key) in enumerate(display):
            r = regions.get(key, {})
            curr = r.get("currency", "")
            floor = r.get("minimum")
            target = r.get("target")
            floor_str = f"{curr} {floor:,}" if isinstance(floor, int) else "—"
            target_str = f"target {target // 1000}k" if isinstance(target, int) else "—"
            border = "border-bottom:1px solid var(--border);" if i < len(display) - 1 else ""
            rows.append(
                f'<tr style="{border}">'
                f'<td style="padding:5px 0;color:var(--text3);font-family:\'DM Mono\',monospace">{code}</td>'
                f'<td style="text-align:right;padding:5px 0;color:var(--text1);font-family:\'DM Mono\',monospace">{floor_str}</td>'
                f'<td style="text-align:right;padding:5px 0;color:var(--green);font-size:9px;padding-left:8px">{target_str}</td>'
                f'</tr>'
            )
        return "".join(rows)
    except Exception:
        return '<tr><td colspan="3" style="color:var(--text3);font-size:9px;padding:5px 0">Config not loaded</td></tr>'


def _git_branch() -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

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

    # ---- stats ----
    col_map: dict[str, list] = {k: [] for k, _, _ in KANBAN_COLUMNS}
    for a in apps:
        col = _kanban_col(a)
        if col:
            col_map[col].append(a)

    total = len(apps)
    n_applied = sum(len(v) for v in col_map.values())
    n_responded = (
        len(col_map["responded"]) + len(col_map["interview"]) + len(col_map["offer"])
    )
    n_interview = len(col_map["interview"])
    n_offer = len(col_map["offer"])
    resp_rate = f"{n_responded / n_applied * 100:.0f}" if n_applied else "—"
    intv_rate = f"{n_interview / n_applied * 100:.0f}" if n_applied else "—"

    resp_delta = f"+{n_responded}" if n_responded else "awaiting replies"
    intv_delta = f"+{n_interview}" if n_interview else "awaiting interviews"

    # ---- chart data ----
    score_dist = _score_distribution(apps)
    tl_labels, tl_values = _timeline_data(apps)
    gate_vals = _gate_counts(apps)
    if sum(gate_vals) == 0:
        gate_vals = [0, 0, 0, 1]  # placeholder to render donut

    score_cfg = {
        "type": "bar",
        "data": {
            "labels": ["1-2", "3-4", "5-6", "7-8", "9-10"],
            "datasets": [
                {"label": "MY", "data": score_dist["MY"], "backgroundColor": "#3a8eff", "borderRadius": 3},
                {"label": "SG", "data": score_dist["SG"], "backgroundColor": "#00d4aa", "borderRadius": 3},
                {"label": "AU", "data": score_dist["AU"], "backgroundColor": "#f5a623", "borderRadius": 3},
                {"label": "EU", "data": score_dist["EU"], "backgroundColor": "#e74c6b", "borderRadius": 3},
            ],
        },
        "options": {
            "responsive": True, "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {
                "x": {"ticks": {"color": "#6878a0", "font": {"size": 9}},
                      "grid": {"color": "rgba(90,124,196,0.1)"}},
                "y": {"ticks": {"color": "#6878a0", "font": {"size": 9}},
                      "grid": {"color": "rgba(90,124,196,0.1)"}, "beginAtZero": True},
            },
        },
    }

    tl_cfg = {
        "type": "line",
        "data": {
            "labels": tl_labels,
            "datasets": [{
                "label": "Applications",
                "data": tl_values,
                "borderColor": "#3a8eff",
                "backgroundColor": "rgba(58,142,255,0.08)",
                "borderWidth": 1.5,
                "pointRadius": 2,
                "pointBackgroundColor": "#3a8eff",
                "tension": 0.4,
                "fill": True,
            }],
        },
        "options": {
            "responsive": True, "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {
                "x": {"ticks": {"color": "#6878a0", "font": {"size": 8},
                                "maxRotation": 0, "autoSkip": True, "maxTicksLimit": 7},
                      "grid": {"color": "rgba(90,124,196,0.08)"}},
                "y": {"ticks": {"color": "#6878a0", "font": {"size": 9}, "stepSize": 1},
                      "grid": {"color": "rgba(90,124,196,0.08)"}, "beginAtZero": True},
            },
        },
    }

    sal_cfg = {
        "type": "doughnut",
        "data": {
            "labels": ["Above target", "Above floor", "Borderline", "Undisclosed"],
            "datasets": [{
                "data": gate_vals,
                "backgroundColor": ["#00d4aa", "#f5a623", "#e74c6b", "#1d3068"],
                "borderColor": "#0f1f42",
                "borderWidth": 2,
            }],
        },
        "options": {
            "responsive": True, "maintainAspectRatio": False,
            "cutout": "70%",
            "plugins": {"legend": {"display": False}},
        },
    }

    score_json = json.dumps(score_cfg, separators=(",", ":"))
    tl_json    = json.dumps(tl_cfg,    separators=(",", ":"))
    sal_json   = json.dumps(sal_cfg,   separators=(",", ":"))

    # ---- kanban HTML ----
    col_colors = {
        "applied": "#3a8eff", "responded": "#f5a623",
        "interview": "#00d4aa", "offer": "#e8b84b", "closed": "#6878a0",
    }
    kanban_html = "".join(
        _kanban_col_html(col_key, col_label, col_map[col_key], col_colors[col_key])
        for col_key, col_label, _ in KANBAN_COLUMNS
    )

    # ---- right panel ----
    attn_items = _attention_items_html(apps, review_ids)
    activity_html = _activity_feed_html(_get_recent_activity())
    salary_rows = _salary_floors_html()
    map_dots_js = _map_dots_js(apps)

    # ---- meta ----
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d · %H:%M MYT")
    branch = _git_branch()
    n_attention = sum(1 for a in apps if _needs_attention(a, review_ids))
    resp_rate_display = f"{resp_rate}%" if resp_rate != "—" else "—%"
    intv_rate_display = f"{intv_rate}%" if intv_rate != "—" else "—%"

    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        "<title>ApplyPilot Tracker</title>\n"
        "<style>\n"
        "@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500&family=DM+Mono:wght@400;500&display=swap');\n"
        "*{box-sizing:border-box;margin:0;padding:0}\n"
        ":root{\n"
        "  --navy-950:#060d1f;--navy-900:#0b1630;--navy-800:#0f1f42;--navy-700:#162654;\n"
        "  --navy-600:#1d3068;--navy-500:#2a4285;--navy-400:#3a5aa8;--navy-300:#5a7cc4;\n"
        "  --navy-200:#8aa4d8;--navy-100:#c0cfed;--navy-50:#e8edf7;\n"
        "  --accent:#3a8eff;--accent2:#00d4aa;--accent3:#f5a623;--accent4:#e74c6b;\n"
        "  --text1:#e8edf7;--text2:#a8b8d8;--text3:#6878a0;\n"
        "  --card:#0f1f42;--border:rgba(90,124,196,0.18);--border2:rgba(90,124,196,0.32);\n"
        "  --green:#00d4aa;--yellow:#f5a623;--red:#e74c6b;--blue:#3a8eff;\n"
        "}\n"
        "body{font-family:'DM Sans',sans-serif;background:var(--navy-950);color:var(--text1);padding:0;min-height:600px}\n"
        ".shell{display:grid;grid-template-rows:auto auto 1fr auto;gap:0;min-height:600px}\n"
        "\n"
        ".topbar{background:var(--navy-900);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;justify-content:space-between}\n"
        ".topbar-left{display:flex;align-items:center;gap:10px}\n"
        ".logo-mark{width:28px;height:28px;background:var(--accent);border-radius:6px;display:flex;align-items:center;justify-content:center}\n"
        ".logo-mark svg{width:16px;height:16px;fill:white}\n"
        ".app-name{font-size:13px;font-weight:500;letter-spacing:0.04em;color:var(--text1)}\n"
        ".app-sub{font-size:11px;color:var(--text3);font-family:'DM Mono',monospace}\n"
        ".topbar-right{display:flex;align-items:center;gap:8px}\n"
        ".badge-live{background:rgba(0,212,170,0.15);color:var(--green);border:1px solid rgba(0,212,170,0.3);border-radius:20px;font-size:10px;padding:3px 10px;font-family:'DM Mono',monospace;letter-spacing:0.06em}\n"
        ".badge-live::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:5px;animation:pulse 2s infinite}\n"
        "@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}\n"
        ".ts{font-size:10px;color:var(--text3);font-family:'DM Mono',monospace}\n"
        "\n"
        ".metrics-row{background:var(--navy-900);border-bottom:1px solid var(--border);padding:12px 20px;display:grid;grid-template-columns:repeat(7,1fr);gap:8px}\n"
        ".metric{background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:10px 12px;position:relative;overflow:hidden}\n"
        ".metric::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--accent-color,var(--accent))}\n"
        ".metric-label{font-size:9px;color:var(--text3);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:4px;font-family:'DM Mono',monospace}\n"
        ".metric-val{font-size:20px;font-weight:500;color:var(--text1);font-family:'DM Mono',monospace;line-height:1}\n"
        ".metric-delta{font-size:9px;margin-top:3px;font-family:'DM Mono',monospace}\n"
        ".delta-up{color:var(--green)}\n"
        ".delta-na{color:var(--text3)}\n"
        "\n"
        ".main{display:grid;grid-template-columns:1fr 320px;gap:0;flex:1}\n"
        ".left-col{padding:16px 20px;border-right:1px solid var(--border);display:flex;flex-direction:column;gap:16px}\n"
        ".right-col{padding:16px;display:flex;flex-direction:column;gap:14px;background:var(--navy-950)}\n"
        "\n"
        ".section-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}\n"
        ".section-title{font-size:10px;font-weight:500;color:var(--text3);letter-spacing:0.1em;text-transform:uppercase;font-family:'DM Mono',monospace}\n"
        ".pill{font-size:9px;background:var(--navy-700);color:var(--text2);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-family:'DM Mono',monospace}\n"
        "\n"
        ".kanban{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}\n"
        ".kol{background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:8px;min-height:120px}\n"
        ".kol-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border)}\n"
        ".kol-label{font-size:9px;font-weight:500;letter-spacing:0.08em;text-transform:uppercase;font-family:'DM Mono',monospace}\n"
        ".kol-count{font-size:9px;background:var(--navy-700);color:var(--text2);border-radius:3px;padding:1px 5px;font-family:'DM Mono',monospace}\n"
        ".kcard{background:var(--navy-700);border:1px solid var(--border);border-radius:5px;padding:7px 8px;margin-bottom:5px;border-left:2px solid var(--salary-color,var(--blue));cursor:pointer;transition:background 0.15s}\n"
        ".kcard:hover{background:var(--navy-600)}\n"
        ".kcard.hidden{display:none}\n"
        ".kcard-co{font-size:10px;font-weight:500;color:var(--text1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n"
        ".kcard-title{font-size:9px;color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px;display:block;text-decoration:none}\n"
        "a.kcard-title:hover{color:var(--accent)}\n"
        ".kcard-foot{display:flex;align-items:center;justify-content:space-between;margin-top:4px}\n"
        ".kcard-score{font-size:9px;font-family:'DM Mono',monospace;color:var(--accent2)}\n"
        ".kcard-loc{font-size:9px;color:var(--text3)}\n"
        ".kcard-days{font-size:8px;color:var(--text3);font-family:'DM Mono',monospace}\n"
        ".empty-col{font-size:9px;color:var(--text3);text-align:center;padding:12px 4px;font-family:'DM Mono',monospace}\n"
        "\n"
        ".chart-wrap{background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:12px}\n"
        ".chart-inner{position:relative;width:100%;height:120px}\n"
        "\n"
        ".map-wrap{background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:12px}\n"
        ".map-inner{position:relative;width:100%;height:160px}\n"
        ".map-legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}\n"
        ".map-dot{display:flex;align-items:center;gap:4px;font-size:9px;color:var(--text2);font-family:'DM Mono',monospace}\n"
        ".dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}\n"
        "\n"
        ".attention-card{background:rgba(231,76,107,0.08);border:1px solid rgba(231,76,107,0.3);border-radius:8px;padding:10px 12px}\n"
        ".attn-head{display:flex;align-items:center;gap:6px;margin-bottom:6px}\n"
        ".attn-icon{width:14px;height:14px;background:var(--red);border-radius:3px;display:flex;align-items:center;justify-content:center;flex-shrink:0}\n"
        ".attn-icon svg{width:8px;height:8px;fill:white}\n"
        ".attn-title{font-size:10px;font-weight:500;color:var(--red);font-family:'DM Mono',monospace;letter-spacing:0.06em}\n"
        ".attn-item{font-size:10px;color:var(--text2);padding:4px 0;border-bottom:1px solid rgba(231,76,107,0.15);display:flex;align-items:flex-start;gap:6px}\n"
        ".attn-item:last-child{border-bottom:none;padding-bottom:0}\n"
        ".attn-bullet{width:4px;height:4px;border-radius:50%;background:var(--red);margin-top:5px;flex-shrink:0}\n"
        "\n"
        ".activity-feed{background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:10px 12px}\n"
        ".activity-item{display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)}\n"
        ".activity-item:last-child{border-bottom:none;padding-bottom:0}\n"
        ".act-dot{width:6px;height:6px;border-radius:50%;margin-top:4px;flex-shrink:0}\n"
        ".act-text{font-size:10px;color:var(--text2);line-height:1.4}\n"
        ".act-co{font-weight:500;color:var(--text1)}\n"
        ".act-time{font-size:9px;color:var(--text3);font-family:'DM Mono',monospace;margin-top:1px}\n"
        "\n"
        ".statusbar{background:var(--navy-900);border-top:1px solid var(--border);padding:6px 20px;display:flex;align-items:center;gap:20px}\n"
        ".sb-item{font-size:9px;color:var(--text3);font-family:'DM Mono',monospace;display:flex;align-items:center;gap:5px}\n"
        ".sb-dot{width:5px;height:5px;border-radius:50%}\n"
        "\n"
        ".region-tabs{display:flex;gap:4px;margin-bottom:10px}\n"
        ".rtab{font-size:9px;padding:3px 8px;border-radius:4px;border:1px solid var(--border);color:var(--text3);cursor:pointer;font-family:'DM Mono',monospace;background:transparent;transition:all 0.15s}\n"
        ".rtab.active{background:var(--navy-600);color:var(--text1);border-color:var(--border2)}\n"
        "</style>\n"
        "</head>\n<body>\n"
        "\n"
        '<div class="shell">\n'
        '  <div class="topbar">\n'
        '    <div class="topbar-left">\n'
        '      <div class="logo-mark">\n'
        '        <svg viewBox="0 0 16 16"><path d="M2 8L8 2l6 6-6 6z"/></svg>\n'
        '      </div>\n'
        '      <div>\n'
        f'        <div class="app-name">ApplyPilot — AR</div>\n'
        f'        <div class="app-sub">agilraj · {escape(branch)}</div>\n'
        '      </div>\n'
        '    </div>\n'
        '    <div class="topbar-right">\n'
        '      <span class="badge-live">GMAIL WATCH ACTIVE</span>\n'
        f'      <span class="ts">{escape(now_str)}</span>\n'
        '    </div>\n'
        '  </div>\n'
        '\n'
        '  <div class="metrics-row">\n'
        '    <div class="metric" style="--accent-color:var(--blue)">\n'
        '      <div class="metric-label">Tracked</div>\n'
        f'      <div class="metric-val">{total}</div>\n'
        '      <div class="metric-delta delta-na">all time</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--blue)">\n'
        '      <div class="metric-label">Applied</div>\n'
        f'      <div class="metric-val">{n_applied}</div>\n'
        '      <div class="metric-delta delta-na">in pipeline</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--yellow)">\n'
        '      <div class="metric-label">Responded</div>\n'
        f'      <div class="metric-val">{n_responded}</div>\n'
        f'      <div class="metric-delta {"delta-up" if n_responded else "delta-na"}">{escape(resp_delta)}</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--green)">\n'
        '      <div class="metric-label">Interviews</div>\n'
        f'      <div class="metric-val">{n_interview}</div>\n'
        f'      <div class="metric-delta {"delta-up" if n_interview else "delta-na"}">{escape(intv_delta)}</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--accent3)">\n'
        '      <div class="metric-label">Offers</div>\n'
        f'      <div class="metric-val">{n_offer}</div>\n'
        '      <div class="metric-delta delta-na">—</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--green)">\n'
        '      <div class="metric-label">Response Rate</div>\n'
        f'      <div class="metric-val">{escape(resp_rate_display)}</div>\n'
        '      <div class="metric-delta delta-na">target ≥ 15%</div>\n'
        '    </div>\n'
        '    <div class="metric" style="--accent-color:var(--accent2)">\n'
        '      <div class="metric-label">Interview Rate</div>\n'
        f'      <div class="metric-val">{escape(intv_rate_display)}</div>\n'
        '      <div class="metric-delta delta-na">target ≥ 30%</div>\n'
        '    </div>\n'
        '  </div>\n'
        '\n'
        '  <div class="main">\n'
        '    <div class="left-col">\n'
        '      <div>\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Application pipeline</span>\n'
        '          <div class="region-tabs">\n'
        '            <button class="rtab active" data-filter="All">All</button>\n'
        '            <button class="rtab" data-filter="MY">MY</button>\n'
        '            <button class="rtab" data-filter="SG">SG</button>\n'
        '            <button class="rtab" data-filter="AU">AU</button>\n'
        '            <button class="rtab" data-filter="EU">EU</button>\n'
        '            <button class="rtab" data-filter="Remote">Remote</button>\n'
        '          </div>\n'
        '        </div>\n'
        f'        <div class="kanban">{kanban_html}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="chart-wrap">\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Score distribution (by region)</span>\n'
        '          <span class="pill">all applications</span>\n'
        '        </div>\n'
        '        <div style="display:flex;gap:8px;margin-bottom:8px">\n'
        '          <span class="map-dot"><span class="dot" style="background:#3a8eff"></span>MY</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#00d4aa"></span>SG</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#f5a623"></span>AU</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#e74c6b"></span>EU</span>\n'
        '        </div>\n'
        '        <div class="chart-inner"><canvas id="scoreChart"></canvas></div>\n'
        '      </div>\n'
        '\n'
        '      <div class="chart-wrap">\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Application timeline</span>\n'
        '          <span class="pill">daily cadence</span>\n'
        '        </div>\n'
        '        <div class="chart-inner"><canvas id="timelineChart"></canvas></div>\n'
        '      </div>\n'
        '\n'
        '      <div class="map-wrap">\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Job location map</span>\n'
        '          <span class="pill">world view</span>\n'
        '        </div>\n'
        '        <div class="map-inner" id="worldMap"></div>\n'
        '        <div class="map-legend">\n'
        '          <span class="map-dot"><span class="dot" style="background:#3a8eff"></span>Malaysia</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#00d4aa"></span>Singapore</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#f5a623"></span>Australia</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#e74c6b"></span>Europe</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#8a6fd8"></span>Remote/SEA</span>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '\n'
        '    <div class="right-col">\n'
        '      <div class="attention-card">\n'
        '        <div class="attn-head">\n'
        '          <div class="attn-icon"><svg viewBox="0 0 8 8"><path d="M4 0L0 8h8z"/></svg></div>\n'
        f'          <span class="attn-title">Needs Attention ({n_attention})</span>\n'
        '        </div>\n'
        f'        {attn_items}\n'
        '      </div>\n'
        '\n'
        '      <div class="activity-feed">\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Recent activity</span>\n'
        '          <span class="pill">live feed</span>\n'
        '        </div>\n'
        f'        {activity_html}\n'
        '      </div>\n'
        '\n'
        '      <div class="chart-wrap">\n'
        '        <div class="section-head">\n'
        '          <span class="section-title">Salary gate breakdown</span>\n'
        '        </div>\n'
        '        <div style="position:relative;width:100%;height:100px">\n'
        '          <canvas id="salaryChart"></canvas>\n'
        '        </div>\n'
        '        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">\n'
        '          <span class="map-dot"><span class="dot" style="background:#00d4aa"></span>Above target</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#f5a623"></span>Above floor</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#e74c6b"></span>Borderline</span>\n'
        '          <span class="map-dot"><span class="dot" style="background:#6878a0"></span>Undisclosed</span>\n'
        '        </div>\n'
        '      </div>\n'
        '\n'
        '      <div style="background:var(--navy-800);border:1px solid var(--border);border-radius:8px;padding:10px 12px">\n'
        '        <div class="section-title" style="margin-bottom:8px">Salary floors active</div>\n'
        '        <table style="width:100%;font-size:10px;border-collapse:collapse">\n'
        f'          {salary_rows}\n'
        '        </table>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '\n'
        '  <div class="statusbar">\n'
        '    <div class="sb-item"><div class="sb-dot" style="background:var(--green)"></div>gmail watch · polling 30min</div>\n'
        '    <div class="sb-item"><div class="sb-dot" style="background:var(--green)"></div>tracker.db · ready</div>\n'
        '    <div class="sb-item"><div class="sb-dot" style="background:var(--green)"></div>gemini 2.5-flash · connected</div>\n'
        f'    <div class="sb-item"><div class="sb-dot" style="background:var(--blue)"></div>{total} applications tracked</div>\n'
        f'    <div class="sb-item"><div class="sb-dot" style="background:{"var(--red)" if n_attention else "var(--green)"}"></div>{n_attention} need attention</div>\n'
        f'    <div class="sb-item" style="margin-left:auto"><div class="sb-dot" style="background:var(--green)"></div>{escape(branch)}</div>\n'
        '  </div>\n'
        '</div>\n'
        '\n'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>\n'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>\n'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/topojson/3.0.2/topojson.min.js"></script>\n'
        '<script>\n'
        f'new Chart(document.getElementById("scoreChart"),{score_json});\n'
        f'new Chart(document.getElementById("timelineChart"),{tl_json});\n'
        f'new Chart(document.getElementById("salaryChart"),{sal_json});\n'
        '\n'
        'document.querySelectorAll(".rtab").forEach(btn=>{\n'
        '  btn.addEventListener("click",function(){\n'
        '    document.querySelectorAll(".rtab").forEach(b=>b.classList.remove("active"));\n'
        '    this.classList.add("active");\n'
        '    const f=this.dataset.filter;\n'
        '    document.querySelectorAll(".kcard").forEach(c=>{\n'
        '      c.classList.toggle("hidden",f!=="All"&&c.dataset.region!==f);\n'
        '    });\n'
        '  });\n'
        '});\n'
        '\n'
        'const mapDiv=document.getElementById("worldMap");\n'
        'const svg=d3.select(mapDiv).append("svg").attr("viewBox","0 0 680 160").attr("width","100%").attr("height","160");\n'
        'const proj=d3.geoNaturalEarth1().scale(110).translate([340,85]);\n'
        'const path=d3.geoPath(proj);\n'
        + map_dots_js + '\n'
        'd3.json("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json").then(world=>{\n'
        '  svg.selectAll("path").data(topojson.feature(world,world.objects.countries).features).join("path")\n'
        '    .attr("d",path).attr("fill","#162654").attr("stroke","#0b1630").attr("stroke-width",0.5);\n'
        '  Object.entries(coords).forEach(([name,[lon,lat]])=>{\n'
        '    const p=proj([lon,lat]);if(!p)return;\n'
        '    const t=targets[name];\n'
        '    svg.append("circle").attr("cx",p[0]).attr("cy",p[1]).attr("r",t.r).attr("fill",t.color).attr("opacity",0.85);\n'
        '    svg.append("circle").attr("cx",p[0]).attr("cy",p[1]).attr("r",t.r+3).attr("fill","none").attr("stroke",t.color).attr("stroke-width",0.8).attr("opacity",0.4);\n'
        '  });\n'
        '}).catch(()=>{\n'
        '  svg.append("text").attr("x",340).attr("y",80).attr("text-anchor","middle").attr("fill","#6878a0").attr("font-size",10).text("map loading...");\n'
        '});\n'
        '</script>\n'
        '</body>\n</html>'
    )

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
