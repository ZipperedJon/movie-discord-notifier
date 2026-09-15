"""Discord webhook posting plus the two message layouts from the reference posts.

Threading note: posting to a Forum/Media channel webhook with `thread_name` makes
Discord create a new forum post. With `?wait=true` the response is the created
message, whose `channel_id` IS that new thread's id. We store it, then every
follow-up for the same movie — the trailer, the reminder — is sent with
`?thread_id=<id>` so it lands in the same thread. No bot token required.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import poster_url
from .db import get_settings

log = logging.getLogger("discord")

EMBED_COLOR = 0x5865F2

TICKET_HEADING = "🎟️Upcoming Ticket"
UPCOMING_HEADING = "🎥Upcoming Movie"


class DiscordError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Discord timestamp helpers.
#
# Discord only accepts ONE style per tag, so a "date (relative)" line is two
# tags, not <t:123:f:r>. Styles used here: F = full long date+time,
# D = long date, R = relative.
# --------------------------------------------------------------------------

def ts(epoch: int, style: str = "F") -> str:
    return f"<t:{int(epoch)}:{style}>"


def ts_with_relative(epoch: int, style: str = "F") -> str:
    return f"{ts(epoch, style)} ({ts(epoch, 'R')})"


def mention(discord_id: str | None) -> str:
    return f"<@{discord_id}>" if discord_id else "No Discord"


def _money(value: int | None) -> str | None:
    return f"${value:,.2f}" if value else None


def _date_epoch(iso_date: str) -> int | None:
    """'2021-09-15' -> epoch seconds at UTC midnight, or None if unparseable."""
    try:
        y, m, d = (int(part) for part in iso_date.split("-"))
        return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
    except (ValueError, AttributeError):
        return None


def _color(record: dict[str, Any]) -> int:
    return record.get("accent_color") or EMBED_COLOR


def _runtime(minutes: int | None) -> str | None:
    """155 -> '2h 35m'."""
    if not minutes:
        return None
    hours, mins = divmod(int(minutes), 60)
    if not hours:
        return f"{mins}m"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


# --------------------------------------------------------------------------
# Webhook transport
# --------------------------------------------------------------------------

def resolve_webhook(kind: str) -> tuple[str, bool]:
    """Return (webhook_url, is_thread_channel) for 'tickets' or 'upcoming'.

    'upcoming' falls back to the tickets webhook when the user checked
    "use the primary webhook".
    """
    s = get_settings()
    if kind == "tickets":
        url, is_thread = s.get("tickets_webhook_url"), bool(s.get("tickets_is_thread"))
    elif kind == "upcoming":
        if s.get("upcoming_use_primary"):
            url, is_thread = s.get("tickets_webhook_url"), bool(s.get("tickets_is_thread"))
        else:
            url, is_thread = s.get("upcoming_webhook_url"), bool(s.get("upcoming_is_thread"))
    else:
        raise DiscordError(f"Unknown webhook kind: {kind}")

    url = (url or "").strip()
    if not url:
        raise DiscordError(
            f"No Discord webhook saved for '{kind}'. Add one on the Settings page."
        )
    return url, is_thread


async def send(
    kind: str,
    *,
    content: str | None = None,
    embeds: list[dict[str, Any]] | None = None,
    username: str | None = None,
    avatar_url: str | None = None,
    thread_name: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Post one message. Returns {'thread_id', 'message_id'} for the created message.

    thread_id wins over thread_name: once a thread exists we post into it rather
    than opening another one.
    """
    url, is_thread = resolve_webhook(kind)

    payload: dict[str, Any] = {"allowed_mentions": {"parse": ["users"]}}
    if content:
        payload["content"] = content[:2000]
    if embeds:
        payload["embeds"] = embeds
    if username:
        payload["username"] = username[:80]
    if avatar_url:
        payload["avatar_url"] = avatar_url

    params: dict[str, str] = {"wait": "true"}
    if thread_id:
        params["thread_id"] = str(thread_id)
    elif is_thread and thread_name:
        # Forum / media channels require a thread name — this opens the post.
        payload["thread_name"] = thread_name[:100]

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, params=params)

    if resp.status_code >= 400:
        raise DiscordError(f"Discord returned {resp.status_code}: {_explain(resp, is_thread)}")

    try:
        body = resp.json()
    except ValueError:
        return {"thread_id": thread_id, "message_id": None}

    # For a forum post, channel_id is the newly created thread — worth keeping.
    # In a PLAIN TEXT channel, channel_id is just the channel itself; storing that
    # as a thread_id would make every later post target a non-thread and fail.
    # So only adopt channel_id when a thread was actually involved.
    created_thread = None
    if thread_id:
        created_thread = str(thread_id)
    elif is_thread and payload.get("thread_name") and body.get("channel_id"):
        created_thread = str(body["channel_id"])

    return {
        "thread_id": created_thread,
        "message_id": str(body.get("id")) if body.get("id") else None,
    }


async def edit_message(
    kind: str,
    message_id: str,
    thread_id: str | None = None,
    *,
    content: str | None = None,
    embeds: list[dict[str, Any]] | None = None,
) -> None:
    """Edit a message this webhook posted earlier.

    PATCH /webhooks/{id}/{token}/messages/{message_id}. Username and avatar are
    fixed at creation and cannot be changed here, which is fine — only the body
    ever changes. A message inside a thread needs ?thread_id= to be found.
    """
    url, _ = resolve_webhook(kind)

    payload: dict[str, Any] = {"allowed_mentions": {"parse": ["users"]}}
    payload["content"] = content[:2000] if content else ""
    payload["embeds"] = embeds or []

    params = {"thread_id": str(thread_id)} if thread_id else {}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.patch(f"{url}/messages/{message_id}", json=payload, params=params)

    if resp.status_code >= 400:
        raise DiscordError(
            f"Could not edit the Discord message ({resp.status_code}): {resp.text[:300]}"
        )


async def delete_message(kind: str, message_id: str, thread_id: str | None = None) -> bool:
    """Delete a message this webhook posted. True if it is gone (or already was).

    Careful: in a Forum/Media channel the FIRST message is the thread starter, so
    deleting it removes the whole thread along with any reactions and replies.
    """
    url, _ = resolve_webhook(kind)
    params = {"thread_id": str(thread_id)} if thread_id else {}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.delete(f"{url}/messages/{message_id}", params=params)

    if resp.status_code in (200, 204, 404):
        return True
    log.warning("Could not delete message %s: %s %s", message_id, resp.status_code, resp.text[:200])
    return False


def _explain(resp: httpx.Response, is_thread: bool) -> str:
    """Turn Discord's terser errors into something actionable."""
    detail = resp.text[:400]
    if "thread_name" in detail or "thread_id" in detail:
        if is_thread:
            detail += (
                "  — this channel may not be a Forum/Media channel; try unchecking "
                "'This is a threads channel' in Settings."
            )
        else:
            detail += (
                "  — this looks like a Forum/Media channel; try checking "
                "'This is a threads channel' in Settings."
            )
    if resp.status_code == 404:
        detail += "  — the webhook URL may have been deleted or mistyped."
    return detail


async def _post_trailer(
    kind: str,
    trailer_url: str,
    thread_id: str | None,
    title: str,
    poster_path: str | None = None,
) -> dict[str, Any]:
    """Follow-up message carrying the bare URL so Discord renders the YouTube player.

    A masked [Trailer](url) link does not unfurl; a bare URL on its own does.
    Carries the same username AND poster avatar as the announcement, otherwise this
    message falls back to the webhook's default icon and looks like a different bot.
    """
    return await send(
        kind,
        content=trailer_url,
        username=title,
        avatar_url=poster_url(poster_path, "w185"),
        thread_id=thread_id,
    )


async def _sync_trailer(kind: str, record: dict[str, Any], thread_id: str | None) -> str | None:
    """Bring the trailer follow-up in line with the record. Returns its message id.

    Editing the existing message keeps it in place when only the URL changed; a
    trailer added or removed after the fact is posted or deleted accordingly.
    """
    existing = record.get("trailer_message_id")
    wanted = record.get("trailer_url")

    if not wanted:
        if existing:
            await delete_message(kind, existing, thread_id)
        return None

    if existing:
        try:
            await edit_message(kind, existing, thread_id, content=wanted)
            return existing
        except DiscordError as exc:
            log.warning("Editing the trailer message failed, reposting: %s", exc)

    result = await _post_trailer(
        kind, wanted, thread_id, record["title"], record.get("poster_path")
    )
    return result.get("message_id")


# --------------------------------------------------------------------------
# Layout 1 — Ticket release announcement
# --------------------------------------------------------------------------

def build_ticket_release(release: dict[str, Any], *, heading: str | None = None) -> dict[str, Any]:
    lines = ["🎟️__**Ticket Info**__"]
    if heading:
        lines.append(heading)
    if release.get("time_known", 1):
        lines.append(f"Tickets drop: {ts_with_relative(release['drop_at'])}")
    else:
        # Only the day is known. Style D is the date with no time, so the post
        # cannot imply an hour nobody has confirmed yet.
        lines.append(f"Tickets drop: {ts(release['drop_at'], 'D')} ({ts(release['drop_at'], 'R')})")
        lines.append("⏳ *Time unknown — will update if found.*")
    lines.append("🎬__**Movie Info**__")

    if release.get("tagline"):
        lines.append(f"🏷️ **{release['title']}:** {release['tagline']}")
    if release.get("release_date"):
        epoch = _date_epoch(release["release_date"])
        lines.append(
            f"📆 **Release Date:** {ts_with_relative(epoch, 'D')}"
            if epoch
            else f"📆 **Release Date:** {release['release_date']}"
        )
    if release.get("genres"):
        lines.append(f"🎭 **Genre:** {release['genres']}")
    if release.get("overview"):
        lines.append(f"💬 **Synopsis:** {release['overview']}")
    if release.get("studios"):
        lines.append(f"🎙️ **Studios:** {release['studios']}")
    if _money(release.get("budget")):
        lines.append(f"💵 **Budget:** {_money(release['budget'])}")

    embed: dict[str, Any] = {
        "title": f"{TICKET_HEADING}: {release['title']}",
        "url": f"https://www.themoviedb.org/movie/{release['tmdb_id']}",
        "description": "\n".join(lines)[:4096],
        "color": _color(release),
    }
    if release.get("poster_path"):
        embed["thumbnail"] = {"url": poster_url(release["poster_path"], "w300")}
    if release.get("backdrop_path"):
        embed["image"] = {"url": poster_url(release["backdrop_path"], "w780")}
    return embed


async def post_ticket_release(
    release: dict[str, Any], *, heading: str | None = None, reuse_thread: bool = True
) -> dict[str, Any]:
    title = f"{TICKET_HEADING}: {release['title']}"
    result = await send(
        "tickets",
        embeds=[build_ticket_release(release, heading=heading)],
        username=release["title"],
        avatar_url=poster_url(release.get("poster_path"), "w185"),
        thread_name=title,
        thread_id=release.get("thread_id") if reuse_thread else None,
    )
    result["trailer_message_id"] = None
    if release.get("trailer_url"):
        trailer = await _post_trailer(
            "tickets",
            release["trailer_url"],
            result["thread_id"],
            release["title"],
            release.get("poster_path"),
        )
        result["trailer_message_id"] = trailer.get("message_id")
    return result


# --------------------------------------------------------------------------
# Layout 2 — Upcoming movie / who has tickets
# --------------------------------------------------------------------------

def _extra_tickets_line(count: int) -> str:
    if not count:
        return "There are no extra tickets."
    if count == 1:
        return "There is **1** extra ticket."
    return f"There are **{count}** extra tickets."


def build_upcoming(showing: dict[str, Any], *, heading: str | None = None) -> dict[str, Any]:
    lines: list[str] = []
    if heading:
        lines.append(heading)

    links = []
    if showing.get("poster_path"):
        links.append(f"[Poster]({poster_url(showing['poster_path'], 'original')})")
    if showing.get("backdrop_path"):
        links.append(f"[Backdrop]({poster_url(showing['backdrop_path'], 'original')})")
    if showing.get("trailer_url"):
        links.append(f"[Trailer]({showing['trailer_url']})")
    if links:
        lines.append(" ◉ ".join(links))

    lines.append("👥 **Got Tickets For:**")
    attendees = showing.get("attendees") or []
    going = [p for p in attendees if not p.get("dropped")]
    dropped = [p for p in attendees if p.get("dropped")]

    if going:
        for person in going:
            lines.append(f"{person['name']} - {mention(person.get('discord_id'))}")
    else:
        lines.append("Nobody has tickets yet.")

    # Someone who backed out is struck through rather than removed, so the thread
    # keeps a visible record of the change.
    for person in dropped:
        lines.append(f"~~{person['name']} - {mention(person.get('discord_id'))}~~")

    lines.append("🎟️ **Extra Tickets:**")
    lines.append(_extra_tickets_line(showing.get("extra_tickets") or 0))

    lines.append("📅 **Time and Date:**")
    lines.append(ts_with_relative(showing["start_at"]))
    if showing.get("end_at"):
        lines.append("to")
        lines.append(ts(showing["end_at"]))
    if _runtime(showing.get("runtime")):
        lines.append(f"⏱️ **Runtime:** {_runtime(showing['runtime'])}")

    if showing.get("theater_name") or showing.get("theater_address"):
        lines.append("📍 **Theater Address:**")
        parts = [p for p in (showing.get("theater_name"), showing.get("theater_address")) if p]
        lines.append(", ".join(parts))

    embed: dict[str, Any] = {
        "title": f"{UPCOMING_HEADING}: {showing['title']}",
        "description": "\n".join(lines)[:4096],
        "color": _color(showing),
    }
    if showing.get("tmdb_id"):
        embed["url"] = f"https://www.themoviedb.org/movie/{showing['tmdb_id']}"
    if showing.get("poster_path"):
        embed["thumbnail"] = {"url": poster_url(showing["poster_path"], "w300")}
    if showing.get("backdrop_path"):
        embed["image"] = {"url": poster_url(showing["backdrop_path"], "w780")}
    return embed


async def post_upcoming(
    showing: dict[str, Any], *, heading: str | None = None, reuse_thread: bool = True
) -> dict[str, Any]:
    title = f"{UPCOMING_HEADING}: {showing['title']}"
    result = await send(
        "upcoming",
        embeds=[build_upcoming(showing, heading=heading)],
        username=showing["title"],
        avatar_url=poster_url(showing.get("poster_path"), "w185"),
        thread_name=title,
        thread_id=showing.get("thread_id") if reuse_thread else None,
    )
    result["trailer_message_id"] = None
    if showing.get("trailer_url"):
        trailer = await _post_trailer(
            "upcoming",
            showing["trailer_url"],
            result["thread_id"],
            showing["title"],
            showing.get("poster_path"),
        )
        result["trailer_message_id"] = trailer.get("message_id")
    return result


# --------------------------------------------------------------------------
# Re-sync after an edit
#
# Preferred path is editing the original message in place. If that message is
# gone (deleted in Discord, or never posted) we post an update instead — into
# the same thread when there is one, so the movie stays in one conversation.
# The trailer is never re-sent on an edit; it is already in the thread.
# --------------------------------------------------------------------------

async def _sync(
    kind: str,
    record: dict[str, Any],
    embed: dict[str, Any],
    full_post,
    *,
    repost: bool = False,
) -> dict[str, Any]:
    # A webhook message's username and avatar are fixed when it is created —
    # PATCH cannot change them. So swapping the movie needs a brand new post,
    # otherwise the old film's name and poster stay on the message forever.
    if repost and record.get("message_id"):
        warning = await _remove_old_post(kind, record)
        # The old thread went with the old starter message, so post as if new —
        # otherwise this would try to post into a thread that no longer exists.
        fresh = await full_post({**record, "thread_id": None, "message_id": None})
        return {**fresh, "edited": False, "reposted": True, "warning": warning}

    if record.get("message_id"):
        try:
            await edit_message(
                kind, record["message_id"], record.get("thread_id"), embeds=[embed]
            )
            trailer_id = await _sync_trailer(kind, record, record.get("thread_id"))
            return {
                "thread_id": record.get("thread_id"),
                "message_id": record["message_id"],
                "trailer_message_id": trailer_id,
                "edited": True,
                "reposted": False,
            }
        except DiscordError as exc:
            log.warning("Editing %s failed, posting an update instead: %s", record["title"], exc)

    if record.get("thread_id"):
        result = await send(
            kind,
            embeds=[embed],
            username=record["title"],
            avatar_url=poster_url(record.get("poster_path"), "w185"),
            thread_id=record["thread_id"],
        )
        result["trailer_message_id"] = await _sync_trailer(kind, record, record["thread_id"])
        return {**result, "edited": False, "reposted": False}

    return {**await full_post(record), "edited": False, "reposted": False}


async def _remove_old_post(kind: str, record: dict[str, Any]) -> str | None:
    """Delete the previous announcement (and its trailer) before reposting."""
    thread_id = record.get("thread_id")

    if record.get("trailer_message_id"):
        await delete_message(kind, record["trailer_message_id"], thread_id)

    ok = await delete_message(kind, record["message_id"], thread_id)
    if not ok:
        return (
            "The previous Discord post could not be deleted, so it may still be there "
            "alongside the new one — remove it by hand if you don't want both."
        )
    return None


async def sync_ticket_release(
    release: dict[str, Any], *, repost: bool = False
) -> dict[str, Any]:
    return await _sync(
        "tickets", release, build_ticket_release(release), post_ticket_release, repost=repost
    )


async def sync_upcoming(showing: dict[str, Any], *, repost: bool = False) -> dict[str, Any]:
    return await _sync(
        "upcoming", showing, build_upcoming(showing), post_upcoming, repost=repost
    )


async def post_test(kind: str) -> dict[str, Any]:
    return await send(
        kind,
        content=(
            "✅ **Movie Discord Notifier** — webhook test.\n"
            f"This is the `{kind}` webhook and it is working."
        ),
        username="Movie Discord Notifier",
        thread_name="Webhook test",
    )
