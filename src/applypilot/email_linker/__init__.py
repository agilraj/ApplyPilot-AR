"""ApplyPilot Email Linker — 3-signal Gmail inbox processor.

Public API:
    process_email(email_data)       — run all signals + fusion on one email dict
    run_poll_loop(callback)         — start Gmail polling daemon
    fetch_recent_messages(n)        — fetch last n messages from Gmail inbox
"""

from applypilot.email_linker.fusion import process_email
from applypilot.email_linker.gmail_client import fetch_recent_messages, run_poll_loop

__all__ = ["process_email", "fetch_recent_messages", "run_poll_loop"]
