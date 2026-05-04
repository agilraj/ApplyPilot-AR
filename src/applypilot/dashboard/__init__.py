"""Dashboard module — CLI table view and HTML tracker dashboard."""

from applypilot.dashboard.cli_view import render_tracker_status
from applypilot.dashboard.html_view import generate_tracker_dashboard, open_tracker_dashboard

__all__ = [
    "render_tracker_status",
    "generate_tracker_dashboard",
    "open_tracker_dashboard",
]
