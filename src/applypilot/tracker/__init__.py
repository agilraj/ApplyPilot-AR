"""ApplyPilot Application Tracker — SQLite CRM logging every application end-to-end."""

from applypilot.tracker.db import get_tracker_connection, init_tracker_db

__all__ = ["get_tracker_connection", "init_tracker_db"]
