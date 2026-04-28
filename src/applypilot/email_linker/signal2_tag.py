"""Signal 2: Tagged email address match.

When applying, the pipeline sends from an address tagged with +AP{id}
(e.g. agilraj96+AP42@gmail.com). Recruiters reply to that address, so
the To: field of their reply contains the tag, letting us deterministically
identify the application.

Pattern:  +{tag_prefix}{numeric_id}@   (tag_prefix from config/gmail.json)
Example:  user+AP1042@gmail.com  →  application_id = 1042

Confidence contribution: +70 (config signal_weights.signal2_tag_match)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from applypilot.tracker.db import get_tracker_connection, init_tracker_db

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GMAIL_CONFIG = _PROJECT_ROOT / "config" / "gmail.json"


def _tag_prefix() -> str:
    try:
        with open(_GMAIL_CONFIG) as f:
            return json.load(f).get("tag_prefix", "AP")
    except Exception:
        return "AP"


def check_signal2(to_addresses: list[str]) -> tuple[Optional[int], Optional[str]]:
    """Return (application_id, tag_str) if a valid tagged address is found.

    Returns (None, None) when no tag pattern matches or the parsed ID is not
    in the applications table.
    """
    prefix = _tag_prefix()
    # Match  +AP<digits>@  anywhere inside an email address
    pattern = re.compile(rf"\+{re.escape(prefix)}(\d+)@", re.IGNORECASE)

    for addr in to_addresses:
        m = pattern.search(addr)
        if not m:
            continue

        app_id = int(m.group(1))
        tag_str = f"+{prefix}{app_id}"

        try:
            conn = get_tracker_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM applications WHERE id = ?", (app_id,)
                ).fetchone()
            except Exception:
                conn = init_tracker_db()
                row = conn.execute(
                    "SELECT id FROM applications WHERE id = ?", (app_id,)
                ).fetchone()

            if row:
                log.debug("Signal2 FIRED: tag=%s → app_id=%d", tag_str, app_id)
                return app_id, tag_str

            log.debug(
                "Signal2: tag %s parsed but app_id=%d not in DB", tag_str, app_id
            )

        except Exception as exc:
            log.warning("Signal2 DB error for tag=%s: %s", tag_str, exc)

    return None, None
