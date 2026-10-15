#!/usr/bin/env python3
"""
One-shot availability check for Mama's Fish House (SevenRooms).
Run on a schedule by GitHub Actions. Behavior:
  - Slots found       -> Discord bot posts an alert
  - Nothing found     -> silent
  - Errors            -> Discord bot posts an error alert (throttled to
                         ~once/hour on scheduled runs; always on manual runs)

Usage:
  python check_mamas.py          # normal check
  python check_mamas.py --test   # send a test message to Discord and exit
  python check_mamas.py --debug  # also print the start of the raw API response

Secrets come from environment variables (GitHub secrets). Use EITHER:
  DISCORD_WEBHOOK                          (simplest)
  DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID   (real bot)
"""
import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import requests

# ----------------------------- CONFIG ---------------------------------
VENUE_SLUG = "mamasfishhouserestaurantinn"
PARTY_SIZE = 6
START_DATE = "2026-12-11"              # first day to check (YYYY-MM-DD)
END_DATE = "2026-12-15"                # last day to check (inclusive)
EARLIEST = "ALL"                       # "ALL" or a time like "16:30"
LATEST = "ALL"                         # "ALL" or a time like "20:30"
# -----------------------------------------------------------------------

API = "https://www.sevenrooms.com/api-yoa/availability/ng/widget/range"
HEADERS = {"User-Agent": "Mozilla/5.0 (personal reservation notifier)"}
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")


def fetch(date_str):
    params = {
        "venue": VENUE_SLUG,
        "party_size": PARTY_SIZE,
        "halo_size_interval": 100,
        "start_date": date_str,            # YYYY-MM-DD
        "num_days": 1,
        "channel": "SEVENROOMS_WIDGET",
        "exclude_pdr": "true",
        "actual_id": "",
        "analytics_session_id": str(uuid.uuid4()),
        "intent": "user_search",
    }
    r = requests.get(API, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def find_slots(node, found=None):
    """Recursively collect bookable time slots (type == 'book')."""
    if found is None:
        found = []
    if isinstance(node, dict):
        if "time" in node and node.get("type") == "book":
            found.append(node["time"])
        for v in node.values():
            find_slots(v, found)
    elif isinstance(node, list):
        for v in node:
            find_slots(v, found)
    return found


def date_range():
    d = date.fromisoformat(START_DATE)
    last = date.fromisoformat(END_DATE)
    while d <= last:
        yield d.isoformat()
        d += timedelta(days=1)


def in_window(t):
    if str(EARLIEST).upper() == "ALL" and str(LATEST).upper() == "ALL":
        return True
    try:
        tt = datetime.strptime(t, "%I:%M %p").strftime("%H:%M")
    except ValueError:
        tt = t[:5]
    if str(EARLIEST).upper() != "ALL" and tt < EARLIEST:
        return False
    if str(LATEST).upper() != "ALL" and tt > LATEST:
        return False
    return True


def notify(msg):
    """Post to Discord via webhook (preferred) or bot token."""
    msg = msg[:1900]
    if WEBHOOK:
        r = requests.post(WEBHOOK, json={"content": msg}, timeout=15)
        r.raise_for_status()
    elif BOT_TOKEN and CHANNEL_ID:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
            headers={
                "Authorization": f"Bot {BOT_TOKEN}",
                "User-Agent": "DiscordBot (mamas-watcher, 1.0)",
            },
            json={"content": msg},
            timeout=15,
        )
        r.raise_for_status()
    else:
        print("Discord not configured; would have sent:\n" + msg)


def should_alert_errors():
    """Manual runs always alert. Scheduled runs alert only in the first
    10 minutes of each hour (UTC) so an outage doesn't spam every 10 min."""
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    return datetime.now(timezone.utc).minute < 10


def main():
    if "--test" in sys.argv:
        notify("🐟 Test message: the Mama's Fish House watcher is connected.")
        return

    errors = []
    dates = list(date_range())
    for day in dates:
        time.sleep(1)  # be polite between requests
        try:
            data = fetch(day)
            if "--debug" in sys.argv:
                print(json.dumps(data)[:3000])
            slots = sorted(set(s for s in find_slots(data) if in_window(s)))
        except Exception as e:
            print(f"{day}: ERROR {e}")
            errors.append(f"{day}: {type(e).__name__}: {e}")
            continue

        print(f"{day}: {slots or 'nothing available'}")
        if slots:
            notify(
                f"🐟 **Mama's Fish House opening!**\n"
                f"{day} — {', '.join(slots)} (party of {PARTY_SIZE})\n"
                f"https://www.sevenrooms.com/reservations/{VENUE_SLUG}"
            )

    if errors and should_alert_errors():
        notify("⚠️ **Mama's watcher error**\n" + "\n".join(errors))

    # Mark the GitHub run as failed only if every date errored.
    if len(errors) == len(dates):
        sys.exit(1)


if __name__ == "__main__":
    main()
