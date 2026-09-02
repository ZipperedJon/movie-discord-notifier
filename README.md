# 🎬 Movie Discord Notifier

A small self-hosted web app for tracking movie ticket drops and movie nights, and
posting them to Discord as formatted announcements.

Two kinds of post:

| | What it is | Where it posts |
|---|---|---|
| 🎟️ **Upcoming Ticket** | "Tickets for *Dune* drop Tuesday 8:40 AM" — a rich embed with the poster, synopsis, genres, studio and budget pulled from TMDB | Tickets webhook |
| 🎥 **Upcoming Movie** | "We have tickets for *Backrooms*" — who's going (with `@mentions`), spare tickets, showtime and theater address | Upcoming webhook |

Both are rich embeds whose left sidebar is tinted with the **average colour of the
movie's poster**, so each movie's post takes on its own look.

Times are posted as native Discord timestamps, so everyone sees them in their own
timezone with a live "in 3 days" counter.

Each announcement is followed by a second message containing the bare trailer URL, so
Discord renders a real YouTube player rather than a plain link. That follow-up carries
the same movie name and poster avatar as the announcement, so the pair reads as one post.

### Finding a trailer

Trailers come from TMDB, which is missing them for plenty of older or smaller films.
When TMDB has nothing, the app searches for `<Title> (<Year>) official trailer` — first
on YouTube, then via Google's "I'm Feeling Lucky" — and takes the top hit. There's also
a **Find** button beside the Trailer URL field to trigger it by hand.

Whatever the route, what gets stored and posted is always a canonical
`youtube.com/watch?v=…`, **never** the search or redirect URL — Discord only renders a
player for a real YouTube link. The same applies to anything you paste: a `youtu.be`
share link, a shorts link, a URL with a timestamp, or a Google lucky link is followed
and turned into the plain watch URL. Anything the search gets wrong you can just
overwrite by hand.

The **end time is filled in from the movie's TMDB runtime** — pick a movie and a start
time and the Ends field populates itself (a 2h 35m film starting at 7:00 PM ends at
9:35 PM). Moving the start carries the end along with it, keeping whatever duration is
set, so if you padded the end for previews that padding survives and the end can never
end up before the start.

---

## Install on a Raspberry Pi (or any systemd Linux)

One line. Installs to `/opt/movie-discord-notifier`, runs it as a locked-down system
user under systemd, and starts it on boot:

```bash
curl -fsSL https://raw.githubusercontent.com/ZipperedJon/movie-discord-notifier/main/install.sh | sudo bash
```

When it finishes it prints your Pi's address — open that and fill in the **Settings**
page. Nothing else to configure.

The installer handles everything: installs `git`/`python3-venv` if missing, creates a
virtualenv, and if a Pillow wheel isn't available for your architecture it pulls the
image build dependencies and retries rather than failing.

| | |
|---|---|
| Status | `sudo systemctl status movie-notifier` |
| Logs | `sudo journalctl -u movie-notifier -f` |
| Restart | `sudo systemctl restart movie-notifier` |
| Update | `sudo /opt/movie-discord-notifier/install.sh --update` |
| Uninstall | `sudo /opt/movie-discord-notifier/install.sh --uninstall` |

Uninstalling keeps your database and copies it to `/root/` first; add `--purge` to
remove it too. Options: `--host=127.0.0.1` (this machine only), `--port=9000`.

> **The app has no login.** It listens on your whole LAN by default so you can reach
> the Pi from your laptop, which is fine at home — but don't port-forward it to the
> internet, since anyone reaching it could read your webhook URLs. Use
> `--host=127.0.0.1` to keep it local to the Pi.

<details>
<summary>Manual install / running it on Windows or macOS</summary>

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python run.py
```

Then open <http://127.0.0.1:8000>. Flags: `--port 9000`, `--host 0.0.0.0` to expose it
on your network, `--reload` while developing.
</details>

## Requirements

- Python 3.10+
- A [TMDB API key](https://www.themoviedb.org/settings/api) (free)
- At least one Discord webhook URL

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

You don't have to leave the Upcoming Movies page to do this: **＋ Add person** and
**＋ Add** (next to the theater dropdown) open a dialog, save straight away, and tick or
select the new entry — everything already filled in on the form is left untouched.

## Threads — everything about one movie stays together

Webhooks can do this without a bot. Posting with `?wait=true` returns the created
message, and for a Forum/Media channel the response's `channel_id` **is** the thread
Discord just opened. The app stores it, then sends every follow-up with
`?thread_id=<id>`:

```
🎥 Upcoming Movie: Backrooms      ← opens the thread
  └─ https://youtube.com/…        ← trailer, unfurls as a player
  └─ ⏰ Starting in about an hour!  ← reminder, same thread
```

Re-posting a movie reuses its original thread rather than opening a second one.

This needs the channel to be a **Forum or Media** channel with "This is a threads
channel" checked. In a plain text channel a webhook cannot create threads — the posts
still go through, just as separate messages in the channel — because thread *creation*
in a text channel is one of the few things that genuinely requires a bot token. Since
this app only ever posts, a webhook covers it. A bot would only be worth it if you
wanted interactivity: reaction/button RSVPs, slash commands, or claiming a spare ticket
by clicking.

## Editing a post after it's up

Every saved entry has an **Edit** button. Change the time, theater, spare tickets,
who's going — even swap the movie — and saving **edits the original Discord message in
place** via `PATCH /webhooks/{id}/{token}/messages/{message_id}`. Same message, same
thread, no duplicate post. The trailer is never re-sent, since it's already in the thread.

The trailer follow-up is kept in step too — change the trailer and that message is
edited, add one and it gets posted, remove one and it is deleted.

If the edit can't go through (the message was deleted in Discord, say), the app posts
the update **into the same thread** instead of opening a new one, and remembers the new
message so the next edit targets that. An entry that was never posted just gets posted
fresh. Untick **Update Discord** to save locally without touching the channel.

### Swapping the movie re-posts

Discord fixes a webhook message's **name and avatar when it is created** — no edit can
change them. So if an edit could only ever patch the body, a swapped movie would keep
the old film's name and poster forever.

Picking a different movie therefore ticks **Re-post instead of editing**: the old post
(and its thread) is deleted and a fresh one goes up, so the name, avatar, thread title,
embed and trailer all match the new movie. You can tick it by hand for the same movie
too. The trade-off is that reactions and replies on the old thread go with it — leave
it unticked to keep the thread and accept a stale name on the message.

### Unsaved changes are hard to miss

Change anything on an edit form — or in a person/theater row — and its **Save button
turns green and pulses**, the row picks up a green edge, and an “Unsaved changes” note
appears. Trying to close the tab or navigate away with edits pending brings up the
browser's “leave site?” confirmation. Saving clears all of it.

### Dropping out is a strikethrough, not a deletion

Unticking someone does not remove them. They stay on the post with their name struck
through, so the thread keeps a visible record of who backed out:

```
👥 Got Tickets For:
Jon - @Jon
Eli - @Eli
~~Yakky - No Discord~~
```

Tick them again and the strike disappears. Under the hood the row is kept and flagged
`dropped`, never deleted.

## The dashboard

A month grid at the top shows what's coming at a glance — amber dots for ticket drops,
blue for movie nights — with arrows to page through months and a **Today** button. Tap
any day to see what's on it. On a phone the grid collapses to dots, since seven columns
can't fit titles, and tapping a day lists them underneath.

Below that, the lists show **only what hasn't happened yet**, soonest first, so finished
movie nights and ticket drops stop filling the page. Nothing is deleted: each section
gets a *"Show N past…"* toggle. A movie you're currently sitting in still counts as
upcoming — it drops off the list once it has finished, not once it has started. The
calendar keeps showing everything, past months included.

## On a phone

The layout is built for a phone as much as a desktop. Under 820px the sidebar becomes a
slide-out drawer behind a hamburger, forms drop to a single column, tap targets grow,
inputs use 16px text so iOS doesn't zoom on focus, and the people/theater tables restack
as cards instead of scrolling sideways. Notches and home indicators are handled with
safe-area insets.

**Add it to your home screen** (iOS: Share → Add to Home Screen; Android: menu → Install)
and it opens full-screen with its own icon, no browser chrome. A manifest and a
deliberately cache-free service worker back that — nothing is cached, so a self-updating
app can never serve you a version it already replaced.

## Calendar

Every showing and ticket release can go in your calendar.

- **📅 Calendar** on any entry downloads a single `.ics`.
- **Settings → Calendar** gives a subscription link (`/calendar.ics`). Subscribe once
  and everything appears automatically and stays in step as you edit — times, theater,
  who's going, spare tickets.

Events carry the theater as the location, who's going (and who dropped out) in the
description, a link back to TMDB, and an alarm matching the movie's reminder setting.
A showing with no end time uses the TMDB runtime. Your calendar app needs to reach the
Pi, so use it on your home network or over a VPN.

## Keeping it up to date

The app updates itself. It checks GitHub every few hours and, if **Update
automatically** is on (it is by default), pulls the new code and restarts —
no SSH, no reinstall. Settings → **Updates** shows the installed commit, has a
**Check for updates** button, and lets you turn auto-update off or change the interval.

Updates only touch code. `data/app.db` — your key, webhooks, people, movies — is never
modified, and the schema migrates itself on the way up.

Under the hood it is `git fetch` + `git reset --hard origin/main`, then a dependency
install, then the process exits so systemd restarts it on the new code. That reset
discards local edits to the install directory, so if you want to hack on your copy,
turn auto-update off. Updating needs a git checkout the service user can write, which
is what `install.sh` produces; a copy without `.git` says so instead of failing oddly.

## Reminders

A background loop checks once a minute and posts, **into the movie's thread**:

- **"Tickets are on sale now!"** when a ticket release's drop time arrives
- **"Starting in about an hour!"** before a showing (default 1 hour, per-movie configurable)

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
  discord.py     Webhook transport, threading, the two message layouts
  colors.py      Average poster colour for the embed sidebar
  scheduler.py   Background reminder loop
  config.py      Paths and constants
templates/       Jinja2 pages
static/          CSS + vanilla JS (no build step)
data/            SQLite database — gitignored
```

## Notes

- Discord accepts only one style per timestamp tag, so a "date (relative)" line is two
  tags — `<t:1788281220:F> (<t:1788281220:R>)` — not `<t:1788281220:f:r>`.
- Poster colour is a true mean over the pixels, which is naturally a little muted. For a
  punchier sidebar, swap `ImageStat.Stat(img).mean` in `app/colors.py` for a dominant-colour
  pick (`img.quantize(8)` then take the most common palette entry).
- `data/app.db` migrates itself on startup — new columns are added in place, so upgrading
  never means deleting your database.
- The app has no login. Bind it to `127.0.0.1` (the default) unless it's behind
  something that handles authentication.
