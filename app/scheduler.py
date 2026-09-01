"""Background reminder loop.

Wakes up once a minute and posts:
  * "Tickets are on sale now" when a ticket release's drop time arrives
  * "Tonight's movie" a configurable number of hours before a showing starts

Each row is marked reminded the moment it fires, so a restart never double-posts.
"""

import asyncio
import logging
import time

from . import discord, store
from .config import SCHEDULER_INTERVAL_SECONDS

log = logging.getLogger("scheduler")


async def run_once() -> None:
    now = int(time.time())

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
            await discord.post_upcoming(
                showing, heading=f"Reminder — {showing['title']} starts in ~{hours}h"
            )
            store.mark_showing_reminded(showing["id"])
            log.info("Posted showing reminder for %s", showing["title"])
        except discord.DiscordError as exc:
            log.warning("Showing reminder for %s failed: %s", showing["title"], exc)


async def loop() -> None:
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the loop alive through any unexpected error
            log.exception("Scheduler pass failed")
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
