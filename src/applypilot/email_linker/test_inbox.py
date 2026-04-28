#!/usr/bin/env python
"""Standalone test: process last 10 Gmail inbox emails and print signal results.

Reads emails from Gmail and runs all 3 signals + fusion. Does NOT write to
the tracker DB — this is a read-only diagnostic script.

Usage:
    python src/applypilot/email_linker/test_inbox.py
    python src/applypilot/email_linker/test_inbox.py --count 20
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Force UTF-8 output so box-drawing and check-mark characters render on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Bootstrap: make src/ importable when running as a standalone script
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

# Load .env before any applypilot imports (LLM client reads env at call time)
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Imports (after path and env setup)
# ---------------------------------------------------------------------------
from applypilot.email_linker.gmail_client import fetch_recent_messages
from applypilot.email_linker.signal1_thread import check_signal1
from applypilot.email_linker.signal2_tag import check_signal2
from applypilot.email_linker.signal3_llm import check_signal3
from applypilot.email_linker.fusion import fuse_signals

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 82


def _signal_status(fired: bool) -> str:
    return "FIRED" if fired else "miss "


def _action_label(action: str) -> str:
    return {
        "auto_link":  "AUTO-LINK  ✓",
        "link_flag":  "LINK+REVIEW ⚠",
        "hold":       "HOLD       –",
    }.get(action, action.upper())


def _conf_bar(confidence: int) -> str:
    """ASCII bar: 0-100 → 0-20 chars."""
    filled = round(confidence / 5)
    return f"[{'█' * filled}{'░' * (20 - filled)}] {confidence:3d}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(count: int = 10) -> None:
    logging.basicConfig(
        level=logging.WARNING,          # suppress debug noise from signals
        format="%(levelname)s %(name)s: %(message)s",
    )

    print()
    print("ApplyPilot — Email Linker Test Runner")
    print(f"Processing last {count} inbox messages against tracker DB")
    print(_DIVIDER)
    print("Authenticating with Gmail...")

    try:
        messages = fetch_recent_messages(max_results=count)
    except Exception as exc:
        print(f"\nERROR fetching Gmail messages: {exc}")
        print(
            "\nMake sure:\n"
            "  1. gmail_credentials.json exists in the project root\n"
            "  2. Run once interactively to complete OAuth (browser will open)\n"
            "  3. GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are set in .env"
        )
        sys.exit(1)

    print(f"Fetched {len(messages)} messages.\n")

    interview_count = 0
    rejection_count = 0
    linked_count = 0

    for i, email_data in enumerate(messages, start=1):
        subject = (email_data.get("subject") or "(no subject)")[:72]
        sender = email_data.get("sender_email", "")
        sender_name = email_data.get("sender_name", "")
        received = (email_data.get("received_date") or "")[:19]
        to_list = email_data.get("to_addresses", [])

        print(f"\n[{i:02d}/{len(messages)}] {subject}")
        print(f"       From  : {sender_name} <{sender}>")
        print(f"       Date  : {received}")
        if to_list:
            print(f"       To    : {', '.join(to_list[:2])}"
                  + (" ..." if len(to_list) > 2 else ""))

        # ── Signal 1 ────────────────────────────────────────────────────────
        s1_id, s1_fired = check_signal1(email_data.get("thread_id", ""))
        s1 = {"application_id": s1_id, "fired": s1_fired}
        s1_detail = f"  → app_id={s1_id}" if s1_fired else ""
        print(f"\n       Signal 1 (Thread) : {_signal_status(s1_fired)}{s1_detail}")

        # ── Signal 2 ────────────────────────────────────────────────────────
        s2_id, s2_tag = check_signal2(to_list)
        s2 = {"application_id": s2_id, "fired": s2_id is not None, "tag_str": s2_tag}
        s2_detail = f"  → tag={s2_tag}  app_id={s2_id}" if s2_id else ""
        print(f"       Signal 2 (Tag)    : {_signal_status(s2_id is not None)}{s2_detail}")

        # ── Signal 3 ────────────────────────────────────────────────────────
        print("       Signal 3 (LLM)    : running …", end="\r", flush=True)
        s3 = check_signal3(
            email_data.get("subject", ""),
            email_data.get("sender_email", ""),
            email_data.get("body_snippet", ""),
        )
        s3_parts = [_signal_status(s3["fired"])]
        if s3["fired"]:
            s3_parts.append(f"app_id={s3['application_id']}")
        if s3["company"]:
            s3_parts.append(f"company='{s3['company']}'")
        if s3["title"]:
            s3_parts.append(f"title='{s3['title']}'")
        s3_parts.append(f"intent={s3['intent']}")
        s3_parts.append(f"strength={s3['match_strength']}")
        s3_parts.append(f"llm_conf={s3['llm_confidence']}")
        print(f"       Signal 3 (LLM)    : {('  ').join(s3_parts)}   ")

        # ── Fusion ──────────────────────────────────────────────────────────
        fusion = fuse_signals(email_data, s1, s2, s3)
        action_str = _action_label(fusion["action"])
        conf_str = _conf_bar(fusion["link_confidence"])
        review_flag = "  ⚠ NEEDS REVIEW" if fusion["requires_review"] else ""
        interview_flag = "  ✓ INTERVIEW" if fusion["is_interview"] else ""
        rejection_flag = "  ✗ REJECTION" if fusion["is_rejection"] else ""

        print()
        print(f"       ┌── Fusion {'─' * 54}")
        print(f"       │  Confidence : {conf_str}")
        print(f"       │  Method     : {fusion['link_method']}")
        print(f"       │  Action     : {action_str}{review_flag}")
        if fusion["application_id"]:
            print(f"       │  Linked to  : app_id={fusion['application_id']}")
        extra = (interview_flag + rejection_flag).strip()
        if extra:
            print(f"       │  Intent     : {extra}")
        if s3["datetime_str"]:
            print(f"       │  Datetime   : {s3['datetime_str']}")
        print(f"       └{'─' * 60}")

        # Tallies
        if fusion["action"] in ("auto_link", "link_flag") and fusion["application_id"]:
            linked_count += 1
        if fusion["is_interview"]:
            interview_count += 1
        if fusion["is_rejection"]:
            rejection_count += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print(_DIVIDER)
    print(f"  Emails processed : {len(messages)}")
    print(f"  Linked           : {linked_count}")
    print(f"  Interview signals: {interview_count}")
    print(f"  Rejection signals: {rejection_count}")
    print(f"  DB modified      : NO  (test mode — use process_email() for live run)")
    print(_DIVIDER)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ApplyPilot Email Linker — test against last N inbox messages"
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Number of inbox messages to process (default: 10)"
    )
    args = parser.parse_args()
    main(count=args.count)
