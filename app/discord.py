"""Discord webhook posting plus the two message layouts from the reference posts."""

from datetime import datetime, timezone
from typing import Any

import httpx

from .config import poster_url
from .db import get_settings

EMBED_COLOR = 0x5865F2


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
) -> None:
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

    # Forum / media channels require a thread_name — the post becomes a new thread.
    if is_thread and thread_name:
        payload["thread_name"] = thread_name[:100]

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, params={"wait": "true"})

    if resp.status_code >= 400:
        detail = resp.text[:400]
        if is_thread and "thread_name" in detail:
            detail += (
                "  — this channel may not be a forum channel; try unchecking "
                "'Threads channel' in Settings."
            )
        if not is_thread and "thread_name" in detail:
            detail += (
                "  — this looks like a forum channel; try checking "
                "'Threads channel' in Settings."
            )
        raise DiscordError(f"Discord returned {resp.status_code}: {detail}")


# --------------------------------------------------------------------------
# Layout 1 — Ticket release announcement (rich embed)
# --------------------------------------------------------------------------

def build_ticket_release(release: dict[str, Any], *, heading: str | None = None) -> dict[str, Any]:
    lines = ["🎟️__**Ticket Info**__"]
    if heading:
        lines.append(heading)
    lines.append(f"Tickets drop: {ts_with_relative(release['drop_at'])}")
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
    if release.get("trailer_url"):
        lines.append(f"🎬 **Trailer:** [Watch on YouTube]({release['trailer_url']})")

    embed: dict[str, Any] = {
        "title": release["title"],
        "url": f"https://www.themoviedb.org/movie/{release['tmdb_id']}",
        "description": "\n".join(lines)[:4096],
        "color": EMBED_COLOR,
    }
    if release.get("poster_path"):
        embed["thumbnail"] = {"url": poster_url(release["poster_path"], "w300")}
    if release.get("backdrop_path"):
        embed["image"] = {"url": poster_url(release["backdrop_path"], "w780")}
    return embed


async def post_ticket_release(release: dict[str, Any], *, heading: str | None = None) -> None:
    await send(
        "tickets",
        embeds=[build_ticket_release(release, heading=heading)],
        username=release["title"],
        avatar_url=poster_url(release.get("poster_path"), "w185"),
        thread_name=release["title"],
    )


# --------------------------------------------------------------------------
# Layout 2 — Upcoming movie / who has tickets (plain content message)
# --------------------------------------------------------------------------

def _extra_tickets_line(count: int) -> str:
    if not count:
        return "There are no extra tickets."
    if count == 1:
        return "There is **1** extra ticket."
    return f"There are **{count}** extra tickets."


def build_upcoming(showing: dict[str, Any], *, heading: str | None = None) -> str:
    title = showing["title"]
    lines = [f"🎥 **{heading or f'Upcoming Movie: {title}'}**"]

    links = []
    if showing.get("trailer_url"):
        links.append(f"[Trailer]({showing['trailer_url']})")
    if showing.get("poster_path"):
        links.append(f"[Poster]({poster_url(showing['poster_path'], 'original')})")
    if showing.get("backdrop_path"):
        links.append(f"[Backdrop]({poster_url(showing['backdrop_path'], 'original')})")
    if links:
        lines.append(" ◉ ".join(links))

    lines.append("👥 **Got Tickets For:**")
    attendees = showing.get("attendees") or []
    if attendees:
        for person in attendees:
            lines.append(f"{person['name']} - {mention(person.get('discord_id'))}")
    else:
        lines.append("Nobody has tickets yet.")

    lines.append("🎟️ **Extra Tickets:**")
    lines.append(_extra_tickets_line(showing.get("extra_tickets") or 0))

    lines.append("📅 **Time and Date:**")
    lines.append(ts_with_relative(showing["start_at"]))
    if showing.get("end_at"):
        lines.append("to")
        lines.append(ts(showing["end_at"]))

    if showing.get("theater_name") or showing.get("theater_address"):
        lines.append("📍 **Theater Address:**")
        parts = [p for p in (showing.get("theater_name"), showing.get("theater_address")) if p]
        lines.append(", ".join(parts))

    return "\n".join(lines)


async def post_upcoming(showing: dict[str, Any], *, heading: str | None = None) -> None:
    embeds = []
    if showing.get("poster_path"):
        embeds.append(
            {"color": EMBED_COLOR, "image": {"url": poster_url(showing["poster_path"], "w500")}}
        )
    await send(
        "upcoming",
        content=build_upcoming(showing, heading=heading),
        embeds=embeds or None,
        username=showing["title"],
        avatar_url=poster_url(showing.get("poster_path"), "w185"),
        thread_name=showing["title"],
    )


async def post_test(kind: str) -> None:
    await send(
        kind,
        content=(
            "✅ **Movie Discord Notifier** — webhook test.\n"
            f"This is the `{kind}` webhook and it is working."
        ),
        username="Movie Discord Notifier",
        thread_name="Webhook test",
    )
