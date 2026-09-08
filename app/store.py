"""CRUD helpers shared by the HTTP routes and the background scheduler."""

from typing import Any

from .db import cursor, rows_to_dicts

# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------

def list_people() -> list[dict[str, Any]]:
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()
        )


def people_by_ids(ids: list[int]) -> list[dict[str, Any]]:
    """Preserves alphabetical order so previews match what actually gets posted."""
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute(
                f"SELECT id, name, discord_id FROM people WHERE id IN ({placeholders}) "
                "ORDER BY name COLLATE NOCASE",
                ids,
            ).fetchall()
        )


def create_person(name: str, discord_id: str | None) -> dict[str, Any]:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO people(name, discord_id) VALUES(?, ?)",
            (name.strip(), (discord_id or "").strip() or None),
        )
        return dict(cur.execute("SELECT * FROM people WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_person(person_id: int, name: str, discord_id: str | None) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE people SET name = ?, discord_id = ? WHERE id = ?",
            (name.strip(), (discord_id or "").strip() or None, person_id),
        )


def delete_person(person_id: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM people WHERE id = ?", (person_id,))


# --------------------------------------------------------------------------
# Theaters
# --------------------------------------------------------------------------

def list_theaters() -> list[dict[str, Any]]:
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute("SELECT * FROM theaters ORDER BY name COLLATE NOCASE").fetchall()
        )


def get_theater(theater_id: int | None) -> dict[str, Any] | None:
    if not theater_id:
        return None
    with cursor() as cur:
        row = cur.execute("SELECT * FROM theaters WHERE id = ?", (theater_id,)).fetchone()
        return dict(row) if row else None


def create_theater(name: str, address: str) -> dict[str, Any]:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO theaters(name, address) VALUES(?, ?)", (name.strip(), address.strip())
        )
        return dict(
            cur.execute("SELECT * FROM theaters WHERE id = ?", (cur.lastrowid,)).fetchone()
        )


def update_theater(theater_id: int, name: str, address: str) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE theaters SET name = ?, address = ? WHERE id = ?",
            (name.strip(), address.strip(), theater_id),
        )


def delete_theater(theater_id: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM theaters WHERE id = ?", (theater_id,))


# --------------------------------------------------------------------------
# Ticket releases
# --------------------------------------------------------------------------

RELEASE_FIELDS = (
    "tmdb_id", "title", "tagline", "overview", "release_date", "genres", "studios",
    "budget", "poster_path", "backdrop_path", "trailer_url", "accent_color",
    "drop_at", "remind",
)


def list_releases() -> list[dict[str, Any]]:
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute("SELECT * FROM ticket_releases ORDER BY drop_at DESC").fetchall()
        )


def get_release(release_id: int) -> dict[str, Any] | None:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM ticket_releases WHERE id = ?", (release_id,)).fetchone()
        return dict(row) if row else None


def create_release(data: dict[str, Any]) -> dict[str, Any]:
    values = [data.get(field) for field in RELEASE_FIELDS]
    placeholders = ", ".join("?" for _ in RELEASE_FIELDS)
    with cursor() as cur:
        cur.execute(
            f"INSERT INTO ticket_releases({', '.join(RELEASE_FIELDS)}) VALUES({placeholders})",
            values,
        )
        new_id = cur.lastrowid
    return get_release(new_id)  # type: ignore[return-value]


def update_release(release_id: int, data: dict[str, Any]) -> dict[str, Any]:
    fields = [f for f in RELEASE_FIELDS if f in data]
    assignments = ", ".join(f"{f} = ?" for f in fields)
    with cursor() as cur:
        if fields:
            cur.execute(
                f"UPDATE ticket_releases SET {assignments} WHERE id = ?",
                [*(data[f] for f in fields), release_id],
            )
    return get_release(release_id)  # type: ignore[return-value]


def delete_release(release_id: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM ticket_releases WHERE id = ?", (release_id,))


def mark_release_posted(release_id: int, thread: dict[str, Any] | None = None) -> None:
    """Records the post, and the thread Discord opened so follow-ups land in it.

    COALESCE keeps the original thread: re-posting a movie should not orphan the
    thread its earlier messages are already in.
    """
    thread = thread or {}
    with cursor() as cur:
        cur.execute(
            "UPDATE ticket_releases SET posted_at = datetime('now'), "
            "thread_id = COALESCE(?, thread_id), message_id = COALESCE(?, message_id), "
            # Not COALESCE: a removed trailer means no message, and keeping the old
            # id would leave us editing a message that is gone.
            "trailer_message_id = ? "
            "WHERE id = ?",
            (
                thread.get("thread_id"),
                thread.get("message_id"),
                thread.get("trailer_message_id"),
                release_id,
            ),
        )


def mark_release_reminded(release_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE ticket_releases SET reminded_at = datetime('now') WHERE id = ?", (release_id,)
        )


def due_release_reminders(now_epoch: int) -> list[dict[str, Any]]:
    """Releases whose drop time has arrived and that have not been reminded yet."""
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute(
                "SELECT * FROM ticket_releases "
                "WHERE remind = 1 AND reminded_at IS NULL AND drop_at <= ?",
                (now_epoch,),
            ).fetchall()
        )


# --------------------------------------------------------------------------
# Showings (upcoming movies)
# --------------------------------------------------------------------------

SHOWING_FIELDS = (
    "tmdb_id", "title", "poster_path", "backdrop_path", "trailer_url", "accent_color",
    "runtime", "release_date", "start_at", "end_at", "theater_id", "extra_tickets",
    "remind", "remind_hours",
)

_SHOWING_SELECT = """
SELECT s.*, t.name AS theater_name, t.address AS theater_address
FROM showings s LEFT JOIN theaters t ON t.id = s.theater_id
"""


def _attach_attendees(cur, showing: dict[str, Any]) -> dict[str, Any]:
    """Attendees, still-going first, each flagged with whether they dropped out."""
    showing["attendees"] = rows_to_dicts(
        cur.execute(
            "SELECT p.id, p.name, p.discord_id, a.dropped FROM showing_attendees a "
            "JOIN people p ON p.id = a.person_id WHERE a.showing_id = ? "
            "ORDER BY a.dropped ASC, p.name COLLATE NOCASE",
            (showing["id"],),
        ).fetchall()
    )
    return showing


def list_showings() -> list[dict[str, Any]]:
    with cursor() as cur:
        rows = rows_to_dicts(cur.execute(f"{_SHOWING_SELECT} ORDER BY s.start_at DESC").fetchall())
        return [_attach_attendees(cur, row) for row in rows]


def get_showing(showing_id: int) -> dict[str, Any] | None:
    with cursor() as cur:
        row = cur.execute(f"{_SHOWING_SELECT} WHERE s.id = ?", (showing_id,)).fetchone()
        return _attach_attendees(cur, dict(row)) if row else None


def create_showing(data: dict[str, Any], attendee_ids: list[int]) -> dict[str, Any]:
    values = [data.get(field) for field in SHOWING_FIELDS]
    placeholders = ", ".join("?" for _ in SHOWING_FIELDS)
    with cursor() as cur:
        cur.execute(
            f"INSERT INTO showings({', '.join(SHOWING_FIELDS)}) VALUES({placeholders})", values
        )
        new_id = cur.lastrowid
        cur.executemany(
            "INSERT OR IGNORE INTO showing_attendees(showing_id, person_id) VALUES(?, ?)",
            [(new_id, pid) for pid in attendee_ids],
        )
    return get_showing(new_id)  # type: ignore[return-value]


def update_showing(showing_id: int, data: dict[str, Any], attendee_ids: list[int]) -> dict[str, Any]:
    """Update a showing and reconcile its attendee list.

    Someone taken off the list is marked dropped, never deleted, so the post can
    strike their name through. Adding them back clears the flag.
    """
    fields = [f for f in SHOWING_FIELDS if f in data]
    assignments = ", ".join(f"{f} = ?" for f in fields)
    values = [data[f] for f in fields]

    with cursor() as cur:
        if fields:
            cur.execute(f"UPDATE showings SET {assignments} WHERE id = ?", [*values, showing_id])

        known = {
            row["person_id"]: row["dropped"]
            for row in cur.execute(
                "SELECT person_id, dropped FROM showing_attendees WHERE showing_id = ?",
                (showing_id,),
            )
        }
        keep = set(attendee_ids)

        for person_id in keep - set(known):
            cur.execute(
                "INSERT INTO showing_attendees(showing_id, person_id, dropped) VALUES(?, ?, 0)",
                (showing_id, person_id),
            )
        for person_id, was_dropped in known.items():
            should_drop = 0 if person_id in keep else 1
            if should_drop != was_dropped:
                cur.execute(
                    "UPDATE showing_attendees SET dropped = ? WHERE showing_id = ? AND person_id = ?",
                    (should_drop, showing_id, person_id),
                )

    return get_showing(showing_id)  # type: ignore[return-value]


def delete_showing(showing_id: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM showings WHERE id = ?", (showing_id,))


def mark_showing_posted(showing_id: int, thread: dict[str, Any] | None = None) -> None:
    thread = thread or {}
    with cursor() as cur:
        cur.execute(
            "UPDATE showings SET posted_at = datetime('now'), "
            "thread_id = COALESCE(?, thread_id), message_id = COALESCE(?, message_id), "
            # Not COALESCE: see mark_release_posted.
            "trailer_message_id = ? "
            "WHERE id = ?",
            (
                thread.get("thread_id"),
                thread.get("message_id"),
                thread.get("trailer_message_id"),
                showing_id,
            ),
        )


def showings_missing_release_date(limit: int = 3) -> list[dict[str, Any]]:
    """Rows saved before release_date existed, for the background backfill.

    NULL means "never looked up". An empty string means "looked up, TMDB has no
    date for it" — those must NOT come back here, or the backfill would refetch
    the same undated movie on every pass forever.
    """
    with cursor() as cur:
        return rows_to_dicts(
            cur.execute(
                "SELECT id, tmdb_id, title FROM showings "
                "WHERE release_date IS NULL ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        )


def set_showing_release_date(showing_id: int, release_date: str | None) -> None:
    # Empty string, not NULL, when TMDB genuinely has no date — otherwise the
    # backfill would keep retrying the same row forever.
    with cursor() as cur:
        cur.execute(
            "UPDATE showings SET release_date = ? WHERE id = ?",
            (release_date or "", showing_id),
        )


def mark_showing_reminded(showing_id: int) -> None:
    with cursor() as cur:
        cur.execute("UPDATE showings SET reminded_at = datetime('now') WHERE id = ?", (showing_id,))


def due_showing_reminders(now_epoch: int) -> list[dict[str, Any]]:
    """Showings inside their reminder window that have not been reminded and have not started."""
    with cursor() as cur:
        rows = rows_to_dicts(
            cur.execute(
                f"{_SHOWING_SELECT} WHERE s.remind = 1 AND s.reminded_at IS NULL "
                "AND s.start_at > ? AND (s.start_at - s.remind_hours * 3600) <= ?",
                (now_epoch, now_epoch),
            ).fetchall()
        )
        return [_attach_attendees(cur, row) for row in rows]
