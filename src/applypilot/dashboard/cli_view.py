"""Terminal table view for applypilot status — tracker-aware.

Called at the end of the existing status command to append a tracked-applications
view grouped by region → status, showing location tier, salary gate result,
score breakdown, and days since applied.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from rich import box
from rich.console import Console
from rich.table import Table

log = logging.getLogger(__name__)
console = Console()

_STATUS_ORDER = [
    "applied", "responded", "interview_scheduled", "interviewing",
    "offer_received", "accepted", "declined",
    "rejected", "ghosted", "withdrawn",
    "discovered", "scored", "tailored",
]

_STATUS_COLOR = {
    "applied": "cyan",
    "responded": "blue",
    "interview_scheduled": "bold green",
    "interviewing": "green",
    "offer_received": "bold yellow",
    "accepted": "bold green",
    "declined": "dim",
    "rejected": "red",
    "ghosted": "dim red",
    "withdrawn": "dim",
    "discovered": "dim",
    "scored": "dim cyan",
    "tailored": "cyan",
}

_GATE_COLOR = {
    "pass": "green",
    "above_target": "green",
    "at_target": "green",
    "disclosed_above_target": "green",
    "disclosed_at_target": "green",
    "borderline": "yellow",
    "flagged": "yellow",
    "undisclosed_proceeding": "yellow",
    "salary_tbc_override": "yellow",
    "disclosed_above_floor_below_target": "yellow",
    "below_floor": "red",
    "disclosed_below_floor": "red",
}

_ATTENTION_GATES = frozenset({
    "borderline", "flagged", "salary_tbc_override",
    "disclosed_above_floor_below_target",
})


def _region(app: dict) -> str:
    country = (app.get("job_location_country") or "").strip()
    tier = (app.get("location_tier") or "").strip()

    if app.get("job_is_remote") or tier == "remote":
        return "Remote"

    sea = {"Malaysia", "Singapore", "Thailand", "Indonesia", "Philippines", "Vietnam", "UAE"}
    eur = {
        "Germany", "Netherlands", "UK", "United Kingdom", "Sweden", "France",
        "Belgium", "Austria", "Switzerland", "Norway", "Denmark", "Spain",
        "Portugal", "Poland", "Czech Republic", "Hungary",
    }
    aus = {"Australia", "New Zealand"}

    if country in sea:
        return "SEA"
    if country in aus:
        return "Australia"
    if country in eur:
        return "Europe"

    raw = (app.get("job_location_raw") or "").lower()
    if "remote" in raw:
        return "Remote"
    for c in sea:
        if c.lower() in raw:
            return "SEA"
    for c in aus:
        if c.lower() in raw:
            return "Australia"
    for c in eur:
        if c.lower() in raw:
            return "Europe"

    return "Other"


def _days_since(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        if days == 0:
            return "today"
        if days == 1:
            return "1d"
        return f"{days}d"
    except Exception:
        return "—"


def _score_breakdown(app: dict) -> str:
    parts = []
    for key, label in [
        ("resume_score", "R"),
        ("role_score", "J"),
        ("location_score", "L"),
        ("salary_score", "S"),
    ]:
        val = app.get(key)
        if val is not None:
            parts.append(f"{label}:{float(val):.0f}")
    return " ".join(parts) if parts else "—"


def _needs_attention(app: dict) -> bool:
    gate = app.get("salary_gate_result") or ""
    if gate in _ATTENTION_GATES:
        return True
    conf = app.get("link_confidence")
    if conf is not None and 30 <= int(conf) < 60:
        return True
    if app.get("status") == "interview_scheduled" and not app.get("prep_package_path"):
        return True
    return False


def _flag_reason(app: dict) -> str:
    reasons = []
    gate = app.get("salary_gate_result") or ""
    if gate in _ATTENTION_GATES:
        reasons.append(f"salary:{gate}")
    conf = app.get("link_confidence")
    if conf is not None and 30 <= int(conf) < 60:
        reasons.append(f"email conf:{conf}%")
    if app.get("status") == "interview_scheduled" and not app.get("prep_package_path"):
        reasons.append("prep missing")
    return ", ".join(reasons)


def render_tracker_status() -> None:
    """Print the tracker-aware application tables to the terminal.

    Appended to the existing `applypilot status` output. Shows:
    - Needs Attention table (salary flags, email confidence gaps, missing prep)
    - Per-region tables with location tier, score breakdown, salary gate, days since applied
    - Summary line with apply/response/interview rates
    """
    try:
        from applypilot.tracker.queries import get_all_applications
    except Exception as exc:
        log.warning("Tracker status unavailable: %s", exc)
        return

    apps = get_all_applications()
    if not apps:
        console.print("\n[dim]No tracked applications found in tracker.db.[/dim]\n")
        return

    console.print("\n[bold]Tracked Applications[/bold]\n")

    # ── Needs Attention ──────────────────────────────────────────────────────
    flagged = [a for a in apps if _needs_attention(a)]
    if flagged:
        attn = Table(
            title="[bold red]Needs Attention[/bold red]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold red",
        )
        attn.add_column("ID", style="dim", width=5)
        attn.add_column("Company", min_width=18)
        attn.add_column("Title", min_width=22)
        attn.add_column("Location", min_width=16)
        attn.add_column("Score", justify="right", width=6)
        attn.add_column("Salary Gate", width=16)
        attn.add_column("Status", width=20)
        attn.add_column("Flag", min_width=24)

        for a in flagged:
            gate = a.get("salary_gate_result") or ""
            gate_color = _GATE_COLOR.get(gate, "dim")
            status = a.get("status") or "—"
            status_color = _STATUS_COLOR.get(status, "white")
            score_str = f"{float(a['fit_score']):.1f}" if a.get("fit_score") else "—"

            attn.add_row(
                str(a.get("id", "")),
                (a.get("company_name") or "")[:20],
                (a.get("job_title") or "")[:26],
                (a.get("job_location_raw") or "—")[:18],
                score_str,
                f"[{gate_color}]{gate or '—'}[/{gate_color}]",
                f"[{status_color}]{status}[/{status_color}]",
                _flag_reason(a),
            )
        console.print(attn)
        console.print()

    # ── Applications by Region → Status ─────────────────────────────────────
    by_region: dict[str, list[dict]] = {}
    for a in apps:
        r = _region(a)
        by_region.setdefault(r, []).append(a)

    def _sort_key(a: dict) -> tuple:
        st = a.get("status") or ""
        si = _STATUS_ORDER.index(st) if st in _STATUS_ORDER else 99
        return (si, -(a.get("fit_score") or 0))

    region_order = ["SEA", "Australia", "Europe", "Remote", "Other"]
    for region in region_order:
        entries = by_region.get(region)
        if not entries:
            continue

        entries.sort(key=_sort_key)

        tbl = Table(
            title=f"[bold cyan]{region}[/bold cyan]",
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold",
        )
        tbl.add_column("ID", style="dim", width=5)
        tbl.add_column("Company", min_width=16)
        tbl.add_column("Title", min_width=22)
        tbl.add_column("Tier", width=12)
        tbl.add_column("Score", justify="right", width=6)
        tbl.add_column("Breakdown", width=18)
        tbl.add_column("Salary Gate", width=22)
        tbl.add_column("Status", width=20)
        tbl.add_column("Applied", width=8)

        for a in entries:
            gate = a.get("salary_gate_result") or ""
            gate_color = _GATE_COLOR.get(gate, "dim")
            status = a.get("status") or "—"
            status_color = _STATUS_COLOR.get(status, "white")
            score_str = f"{float(a['fit_score']):.1f}" if a.get("fit_score") else "—"
            tier = a.get("location_tier") or "—"
            days = _days_since(a.get("applied_date"))
            breakdown = _score_breakdown(a)

            tbl.add_row(
                str(a.get("id", "")),
                (a.get("company_name") or "")[:18],
                (a.get("job_title") or "")[:26],
                tier,
                score_str,
                breakdown,
                f"[{gate_color}]{gate or '—'}[/{gate_color}]",
                f"[{status_color}]{status}[/{status_color}]",
                days,
            )

        console.print(tbl)
        console.print()

    # ── Summary line ─────────────────────────────────────────────────────────
    total = len(apps)

    _active_statuses = frozenset({
        "applied", "responded", "interview_scheduled", "interviewing",
        "offer_received", "accepted", "declined", "rejected", "ghosted", "withdrawn",
    })
    applied = sum(1 for a in apps if (a.get("status") or "") in _active_statuses)
    responded = sum(
        1 for a in apps
        if (a.get("status") or "") in {
            "responded", "interview_scheduled", "interviewing",
            "offer_received", "accepted", "declined",
        }
    )
    interviewing = sum(
        1 for a in apps
        if (a.get("status") or "") in {"interview_scheduled", "interviewing"}
    )

    resp_rate = f"{responded / applied * 100:.0f}%" if applied else "—"
    intv_rate = f"{interviewing / applied * 100:.0f}%" if applied else "—"

    console.print(
        f"[bold]Summary:[/bold] {total} tracked  |  "
        f"{applied} applied  |  "
        f"Response rate: [cyan]{resp_rate}[/cyan]  |  "
        f"Interview rate: [green]{intv_rate}[/green]"
    )
    if flagged:
        console.print(
            f"[bold red]{len(flagged)} item(s) need attention[/bold red]  — "
            "run [bold]applypilot dashboard[/bold] for details"
        )
    console.print()
