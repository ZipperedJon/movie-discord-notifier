"""Paths and constants. Everything user-specific lives in DATA_DIR, which is gitignored."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
TMDB_API_KEY_URL = "https://www.themoviedb.org/settings/api"

# How often the background scheduler wakes up to look for due reminders.
SCHEDULER_INTERVAL_SECONDS = 60

DATA_DIR.mkdir(parents=True, exist_ok=True)


def poster_url(path: str | None, size: str = "w500") -> str | None:
    return f"{TMDB_IMAGE_BASE}/{size}{path}" if path else None
