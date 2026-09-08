"""Background reminder loop.

Wakes up once a minute and posts:
  * "Tickets are on sale now" when a ticket release's drop time arrives
  * "Tonight's movie" a configurable number of hours before a showing starts

Each row is marked reminded the moment it fires, so a restart never double-posts.
"""

import asyncio
import logging
import time

from . import discord, store, tmdb, updater
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


async def backfill_release_dates() -> None:
    """Fill release_date on showings saved before that column existed.

    A few per pass so a long list trickles in without hammering TMDB, and
    skipped entirely without a key. Rows TMDB has no date for get an empty
    string so they are not retried forever.
    """
    if not (get_settings().get("tmdb_api_key") or "").strip():
        return

    for row in store.showings_missing_release_date(limit=3):
        try:
            movie = await tmdb.get_movie(row["tmdb_id"], find_trailer=False)
        except tmdb.TMDBError as exc:
            log.warning("Backfill for %s failed: %s", row["title"], exc)
            return  # a bad key or no network: stop, try again next pass
        store.set_showing_release_date(row["id"], movie.get("release_date"))
        log.info("Backfilled release date for %s: %s", row["title"], movie.get("release_date"))


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
            await backfill_release_dates()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Release-date backfill failed")

        try:
            await maybe_auto_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Auto-update check failed")

        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
