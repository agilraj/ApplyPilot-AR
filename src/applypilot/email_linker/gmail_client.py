"""Gmail API client: OAuth2 authentication, message fetching, and poll daemon.

Credentials: gmail_credentials.json (project root, from Google Cloud Console)
Token cache: gmail_token.json (project root, auto-created on first auth)
Config:       config/gmail.json  — scopes and poll_interval_minutes
Env override: GMAIL_BASE_EMAIL   — overrides base_email in gmail.json
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CREDENTIALS_FILE = _PROJECT_ROOT / "gmail_credentials.json"
_TOKEN_FILE = _PROJECT_ROOT / "gmail_token.json"
_GMAIL_CONFIG_FILE = _PROJECT_ROOT / "config" / "gmail.json"
_PID_FILE = _PROJECT_ROOT / "gmail_linker.pid"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    with open(_GMAIL_CONFIG_FILE) as f:
        return json.load(f)


def _get_scopes() -> list[str]:
    return _load_config().get("scopes", [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ])


# ---------------------------------------------------------------------------
# OAuth2
# ---------------------------------------------------------------------------

def authenticate() -> Credentials:
    """Return valid OAuth2 credentials. Runs browser flow on first call.

    Saves the refreshed token to gmail_token.json so subsequent calls
    skip the browser consent screen.
    """
    scopes = _get_scopes()
    creds: Optional[Credentials] = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            log.info("Gmail token refreshed")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDENTIALS_FILE), scopes
            )
            creds = flow.run_local_server(port=0)
            log.info("Gmail OAuth2 completed")

        with open(_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        log.info("Gmail token saved to %s", _TOKEN_FILE)

    return creds


def get_gmail_service():
    """Return an authenticated Gmail API service object."""
    creds = authenticate()
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    def _extract(part: dict) -> str:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            result = _extract(sub)
            if result:
                return result
        return ""

    return _extract(payload)


def _parse_display_address(raw: str) -> tuple[str, str]:
    """Parse 'Display Name <email>' or bare email into (name, email)."""
    m = re.match(r'^(.*?)<([^>]+)>', raw.strip())
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip().lower()
    bare = raw.strip().lower()
    return "", bare


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def parse_message(msg_data: dict) -> dict:
    """Convert a raw Gmail API message into a normalised email dict.

    Returned keys:
      id, thread_id, received_date (ISO-8601 str),
      sender_email, sender_name, subject,
      body_snippet (max 500 chars),
      to_addresses (list[str])
    """
    payload = msg_data.get("payload", {})
    headers = payload.get("headers", [])

    from_raw = _get_header(headers, "From")
    sender_name, sender_email = _parse_display_address(from_raw)
    to_raw = _get_header(headers, "To")
    date_raw = _get_header(headers, "Date")
    subject = _get_header(headers, "Subject")

    # Parse RFC-2822 date
    try:
        from email.utils import parsedate_to_datetime
        received_date = parsedate_to_datetime(date_raw).isoformat()
    except Exception:
        received_date = datetime.now(timezone.utc).isoformat()

    body = _decode_body(payload) or msg_data.get("snippet", "")
    body_snippet = body[:500]

    to_addresses = [t.strip() for t in to_raw.split(",") if t.strip()]

    return {
        "id": msg_data["id"],
        "thread_id": msg_data["threadId"],
        "received_date": received_date,
        "sender_email": sender_email,
        "sender_name": sender_name,
        "subject": subject,
        "body_snippet": body_snippet,
        "to_addresses": to_addresses,
    }


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_recent_messages(max_results: int = 10) -> list[dict]:
    """Fetch and parse the most recent messages from the Gmail inbox.

    Returns a list of normalised email dicts (see parse_message).
    """
    service = get_gmail_service()
    list_resp = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        labelIds=["INBOX"],
    ).execute()

    raw_list = list_resp.get("messages", [])
    parsed: list[dict] = []

    for item in raw_list:
        try:
            msg_data = service.users().messages().get(
                userId="me", id=item["id"], format="full"
            ).execute()
            parsed.append(parse_message(msg_data))
            time.sleep(0.1)  # stay well within Gmail API quota
        except Exception as exc:
            log.warning("Could not fetch message %s: %s", item["id"], exc)

    log.info("Fetched %d messages from Gmail inbox", len(parsed))
    return parsed


# ---------------------------------------------------------------------------
# Poll daemon
# ---------------------------------------------------------------------------

def _is_already_running() -> bool:
    if not _PID_FILE.exists():
        return False
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)   # signal 0: check existence only
        return True
    except (ValueError, OSError, ProcessLookupError):
        return False


def _write_pid() -> None:
    _PID_FILE.write_text(str(os.getpid()))


def _clear_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def run_poll_loop(on_email: Callable[[dict], None]) -> None:
    """Start the Gmail polling daemon.

    Calls on_email(email_dict) for each previously-unseen message.
    Writes a PID file so only one instance runs at a time.
    """
    if _is_already_running():
        log.error(
            "Gmail linker daemon is already running. "
            "Remove %s to force a restart.", _PID_FILE
        )
        return

    _write_pid()
    cfg = _load_config()
    interval_sec = cfg.get("poll_interval_minutes", 30) * 60
    seen_ids: set[str] = set()

    log.info(
        "Gmail linker started (PID %d). Poll interval: %d min",
        os.getpid(), interval_sec // 60
    )

    try:
        while True:
            try:
                messages = fetch_recent_messages(max_results=20)
                for msg in messages:
                    if msg["id"] not in seen_ids:
                        seen_ids.add(msg["id"])
                        try:
                            on_email(msg)
                        except Exception as exc:
                            log.warning("on_email callback error for %s: %s", msg["id"], exc)
            except Exception as exc:
                log.error("Gmail poll error: %s", exc)

            time.sleep(interval_sec)
    finally:
        _clear_pid()
        log.info("Gmail linker stopped")
