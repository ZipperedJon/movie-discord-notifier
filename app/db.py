"""SQLite storage. The DB file lives in data/ and is gitignored, so API keys and
webhook URLs from local testing never travel with the repo."""

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    discord_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS theaters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    address    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- "Ticket Releases": announce when tickets go on sale for a movie.
CREATE TABLE IF NOT EXISTS ticket_releases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id       INTEGER NOT NULL,
    title         TEXT NOT NULL,
    tagline       TEXT,
    overview      TEXT,
    release_date  TEXT,
    genres        TEXT,
    studios       TEXT,
    budget        INTEGER,
    poster_path   TEXT,
    backdrop_path TEXT,
    trailer_url   TEXT,
    accent_color  INTEGER,                -- average poster colour, embed sidebar
    drop_at       INTEGER NOT NULL,       -- unix epoch seconds
    thread_id     TEXT,                   -- forum thread Discord created for this post
    message_id    TEXT,
    posted_at     TEXT,
    reminded_at   TEXT,
    remind        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- "Upcoming Movies": a showing we hold tickets for.
CREATE TABLE IF NOT EXISTS showings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id        INTEGER NOT NULL,
    title          TEXT NOT NULL,
    poster_path    TEXT,
    backdrop_path  TEXT,
    trailer_url    TEXT,
    accent_color   INTEGER,               -- average poster colour, embed sidebar
    runtime        INTEGER,               -- minutes, from TMDB
    start_at       INTEGER NOT NULL,      -- unix epoch seconds
    end_at         INTEGER,               -- unix epoch seconds, defaults to start + runtime
    theater_id     INTEGER REFERENCES theaters(id) ON DELETE SET NULL,
    extra_tickets  INTEGER NOT NULL DEFAULT 0,
    thread_id      TEXT,                  -- forum thread Discord created for this post
    message_id     TEXT,
    posted_at      TEXT,
    reminded_at    TEXT,
    remind         INTEGER NOT NULL DEFAULT 1,
    remind_hours   INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Removing someone from a showing sets dropped = 1 rather than deleting the row,
-- so the Discord post can strike their name through instead of silently losing it.
CREATE TABLE IF NOT EXISTS showing_attendees (
    showing_id INTEGER NOT NULL REFERENCES showings(id) ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id)   ON DELETE CASCADE,
    dropped    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (showing_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_releases_drop ON ticket_releases(drop_at);
CREATE INDEX IF NOT EXISTS idx_showings_start ON showings(start_at);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "tmdb_api_key": "",
    "tickets_webhook_url": "",
    "tickets_is_thread": False,
    "upcoming_use_primary": True,
    "upcoming_webhook_url": "",
    "upcoming_is_thread": False,
    "auto_update": True,
    "update_interval_hours": 6,
    "last_update_check": "",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT EXISTS",
# so check PRAGMA table_info first and only add what is missing. This lets an
# existing data/app.db upgrade in place instead of needing to be deleted.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("ticket_releases", "accent_color", "INTEGER"),
    ("ticket_releases", "thread_id", "TEXT"),
    ("ticket_releases", "message_id", "TEXT"),
    ("showings", "accent_color", "INTEGER"),
    ("showings", "thread_id", "TEXT"),
    ("showings", "message_id", "TEXT"),
    ("showings", "runtime", "INTEGER"),
    ("showing_attendees", "dropped", "INTEGER NOT NULL DEFAULT 0"),
    # The trailer follow-up message, so an edit can update or replace it.
    ("showings", "trailer_message_id", "TEXT"),
    ("ticket_releases", "trailer_message_id", "TEXT"),
    # The movie's own theatrical release date, so the list can sort by it.
    # Backfilled from TMDB for rows saved before this existed.
    ("showings", "release_date", "TEXT"),
)


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# --------------------------------------------------------------------------
# Settings are stored as JSON values in a key/value table so adding a new
# setting later never needs a migration.
# --------------------------------------------------------------------------

def get_settings() -> dict[str, Any]:
    values = dict(DEFAULT_SETTINGS)
    with cursor() as cur:
        for row in cur.execute("SELECT key, value FROM settings"):
            if row["key"] in values:
                try:
                    values[row["key"]] = json.loads(row["value"])
                except (TypeError, json.JSONDecodeError):
                    values[row["key"]] = row["value"]
    return values


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    with cursor() as cur:
        for key, value in updates.items():
            if key not in DEFAULT_SETTINGS:
                continue
            cur.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
    return get_settings()


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
