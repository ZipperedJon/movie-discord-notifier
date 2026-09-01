# 🎬 Movie Discord Notifier

A small self-hosted web app for tracking movie ticket drops and movie nights, and
posting them to Discord as formatted announcements.

Two kinds of post:

| | What it is | Where it posts |
|---|---|---|
| 🎟️ **Ticket Releases** | "Tickets for *Dune* drop Tuesday 8:40 AM" — a rich embed with the poster, synopsis, genres, studio and budget pulled from TMDB | Tickets webhook |
| 🎥 **Upcoming Movies** | "We have tickets for *Backrooms*" — who's going (with `@mentions`), spare tickets, showtime and theater address | Upcoming webhook |

Times are posted as native Discord timestamps, so everyone sees them in their own
timezone with a live "in 3 days" counter.

---

## Requirements

- Python 3.10+
- A [TMDB API key](https://www.themoviedb.org/settings/api) (free)
- At least one Discord webhook URL

## Setup

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Then open <http://127.0.0.1:8000> and fill in the **Settings** page.

Useful flags: `python run.py --port 9000 --reload`, or `--host 0.0.0.0` to reach it
from other machines on your network.

## Settings

Three inputs, all stored locally and all persisted across restarts:

1. **Tickets webhook** — where ticket-release announcements go, plus a
   *"this is a threads channel"* checkbox.
2. **Upcoming movies webhook** — either reuse the primary webhook above, or give a
   separate URL with its own threads checkbox.
3. **TMDB API key** — powers search, posters, synopses and trailer lookup. Both the
   v3 API key and the v4 read access token are accepted.

Each field has a **Test** button that proves the credential works before you rely on it.

### The "threads channel" checkbox

Check it when the target channel is a **Forum** or **Media** channel. Discord requires
a thread name for those, so each post opens a new thread named after the movie. Leave
it unchecked for a normal text channel. If you pick wrong, the error message from
Discord tells you which way to flip it.

## Getting a Discord user ID

The **People & Theaters** page stores each person's numeric Discord ID so posts can
mention them (`Jon - <@123456789012345678>`). To find one: Discord → Settings →
Advanced → **Developer Mode** on, then right-click a user → **Copy User ID**.

Leave the ID blank for someone who isn't on Discord and they'll show as
`Yakky - No Discord`.

## Reminders

A background loop checks once a minute and posts:

- **"Tickets are on sale now!"** when a ticket release's drop time arrives
- **"Starts in ~3h"** before a showing (the lead time is per-movie and configurable)

Each row is marked as reminded the instant it fires, so restarting the app never
double-posts. Reminders only fire while the app is running — if it's off at the
scheduled moment, that reminder is skipped rather than posted late.

## Data & privacy

Everything lives in a SQLite database at `data/app.db`:

- your TMDB API key and Discord webhook URLs
- people and their Discord IDs
- theaters, ticket releases, showings and who has tickets

**`data/` is gitignored.** Cloning this repo gets you the code and an empty database —
never anyone else's API key, webhooks, or Discord IDs. To back up or move your setup,
copy `data/app.db`. To start over, delete it and restart.

## Project layout

```
app/
  main.py        FastAPI routes — pages + JSON API
  db.py          SQLite schema, settings key/value store
  store.py       CRUD shared by routes and the scheduler
  tmdb.py        TMDB search, movie details, YouTube trailer pick
  discord.py     Webhook transport + the two message layouts
  scheduler.py   Background reminder loop
  config.py      Paths and constants
templates/       Jinja2 pages
static/          CSS + vanilla JS (no build step)
data/            SQLite database — gitignored
```

## Notes

- Discord accepts only one style per timestamp tag, so a "date (relative)" line is two
  tags — `<t:1788281220:F> (<t:1788281220:R>)` — not `<t:1788281220:f:r>`.
- The upcoming-movie post links the trailer as `[Trailer](url)`. If your server doesn't
  expand that into a YouTube player, change `build_upcoming` in `app/discord.py` to append
  the bare URL on its own line, which always unfurls.
- The app has no login. Bind it to `127.0.0.1` (the default) unless it's behind
  something that handles authentication.
