"""iCalendar (.ics) output for showings and ticket releases.

Two uses: download a single event, or subscribe a phone/desktop calendar to the
whole feed so it stays in step as things are added and edited.

Named ics.py rather than calendar.py so it can't shadow the stdlib module.
"""

from datetime import datetime, timezone
from typing import Any

PRODID = "-//Movie Discord Notifier//EN"
DEFAULT_MINUTES = 120  # when a showing has no end time and no known runtime


def _stamp(epoch: int | float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    """RFC 5545 §3.3.11: backslash, semicolon, comma and newline are special."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Fold to 75 octets per RFC 5545, counting bytes so UTF-8 survives.

    Emoji are multi-byte, and splitting mid-character would corrupt the file in
    strict parsers, so the break has to land on a character boundary.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]

    out, current, size = [], [], 0
    limit = 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            out.append("".join(current))
            current, size = [char], width + 1  # +1 for the leading space
            limit = 75
        else:
            current.append(char)
            size += width
    if current:
        out.append("".join(current))
    return [out[0]] + [" " + part for part in out[1:]]


def _event(
    *,
    uid: str,
    start: int,
    end: int,
    summary: str,
    description: str | None = None,
    location: str | None = None,
    url: str | None = None,
    alarm_minutes: int | None = None,
) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(datetime.now(tz=timezone.utc).timestamp())}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if url:
        lines.append(f"URL:{_escape(url)}")
    if alarm_minutes:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-PT{int(alarm_minutes)}M",
            f"DESCRIPTION:{_escape(summary)}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def showing_event(showing: dict[str, Any]) -> list[str]:
    start = int(showing["start_at"])
    end = showing.get("end_at")
    if not end:
        minutes = showing.get("runtime") or DEFAULT_MINUTES
        end = start + int(minutes) * 60

    going = [a["name"] for a in (showing.get("attendees") or []) if not a.get("dropped")]
    dropped = [a["name"] for a in (showing.get("attendees") or []) if a.get("dropped")]

    body = []
    if going:
        body.append("Going: " + ", ".join(going))
    if dropped:
        body.append("Dropped out: " + ", ".join(dropped))
    if showing.get("extra_tickets"):
        body.append(f"Extra tickets: {showing['extra_tickets']}")
    if showing.get("runtime"):
        body.append(f"Runtime: {showing['runtime']} min")
    if showing.get("trailer_url"):
        body.append(f"Trailer: {showing['trailer_url']}")

    place = ", ".join(
        p for p in (showing.get("theater_name"), showing.get("theater_address")) if p
    )

    return _event(
        uid=f"showing-{showing['id']}@movie-discord-notifier",
        start=start,
        end=int(end),
        summary=f"🎥 {showing['title']}",
        description="\n".join(body) or None,
        location=place or None,
        url=f"https://www.themoviedb.org/movie/{showing['tmdb_id']}",
        alarm_minutes=(showing.get("remind_hours") or 1) * 60 if showing.get("remind") else None,
    )


def release_event(release: dict[str, Any]) -> list[str]:
    """Tickets going on sale is a moment, so give it a short block."""
    start = int(release["drop_at"])

    body = []
    if release.get("genres"):
        body.append(release["genres"])
    if release.get("release_date"):
        body.append(f"In cinemas: {release['release_date']}")
    if release.get("trailer_url"):
        body.append(f"Trailer: {release['trailer_url']}")

    return _event(
        uid=f"release-{release['id']}@movie-discord-notifier",
        start=start,
        end=start + 15 * 60,
        summary=f"🎟️ Tickets on sale: {release['title']}",
        description="\n".join(body) or None,
        url=f"https://www.themoviedb.org/movie/{release['tmdb_id']}",
        alarm_minutes=30 if release.get("remind") else None,
    )


def build(
    events: list[list[str]], name: str = "Movie Nights", refresh_minutes: int = 60
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        f"X-PUBLISHED-TTL:PT{refresh_minutes}M",
        f"REFRESH-INTERVAL;VALUE=DURATION:PT{refresh_minutes}M",
    ]
    for event in events:
        lines.extend(event)
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    # CRLF endings are required by RFC 5545; some parsers reject bare LF.
    return "\r\n".join(folded) + "\r\n"
