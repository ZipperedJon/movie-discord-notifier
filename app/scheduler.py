"""Background reminder loop.

Wakes up once a minute and posts:
  * "Tickets are on sale now" when a ticket release's drop time arrives
  * "Tonight's movie" a configurable number of hours before a showing starts

Each row is marked reminded the moment it fires, so a restart never double-posts.
"""

import asyncio
import logging
import time

from . import discord, store, updater
from .config import SCHEDULER_INTERVAL_SECONDS
from .db import get_settings

log = logging.getLogger("scheduler")

_last_update_check = 0.0


async def run_once() -> None:
    now = int(time.time())

    # Reminders reuse the thread the original announcement opened (post_* passes
    # the stored thread_id through), so the whole movie stays in one conversation.
    for release in store.due_release_reminders(now):
        try:
            await discord.post_ticket_release(
                release, heading="🚨 **Tickets are on sale now!**"
            )
            store.mark_release_reminded(release["id"])
            log.info("Posted ticket-drop reminder for %s", release["title"])
        except discord.DiscordError as exc:
            log.warning("Ticket reminder for %s failed: %s", release["title"], exc)

    for showing in store.due_showing_reminders(now):
        try:
            hours = showing.get("remind_hours") or 0
            when = "in about an hour" if hours == 1 else f"in about {hours} hours"
            await discord.post_upcoming(
                showing, heading=f"⏰ **Starting {when}!**"
            )
            store.mark_showing_reminded(showing["id"])
            log.info("Posted showing reminder for %s", showing["title"])
        except discord.DiscordError as exc:
            log.warning("Showing reminder for %s failed: %s", showing["title"], exc)


async def maybe_auto_update() -> None:
    """Check GitHub on the configured interval, not on every minute-long pass."""
    global _last_update_check

    hours = get_settings().get("update_interval_hours") or 6
    due_after = max(1, int(hours)) * 3600
    now = time.monotonic()

    # Skip the very first pass so a restart loop can never hammer GitHub.
    if _last_update_check == 0.0:
        _last_update_check = now
        return
    if now - _last_update_check < due_after:
        return

    _last_update_check = now
    await updater.auto_update_once()


async def loop() -> None:
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the loop alive through any unexpected error
            log.exception("Scheduler pass failed")

        try:
            await maybe_auto_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Auto-update check failed")

        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
