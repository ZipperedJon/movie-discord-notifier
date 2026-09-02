"""FastAPI app: pages, JSON API, and the background reminder loop."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import colors, discord, ics, scheduler, store, tmdb, trailers, updater
from .config import STATIC_DIR, TEMPLATES_DIR, TMDB_API_KEY_URL, poster_url
from .db import get_settings, init_db, save_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler.loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Movie Discord Notifier", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["poster_url"] = poster_url


def _static_version() -> str:
    """Newest mtime across static/, appended to asset URLs as ?v=.

    Without this, a browser keeps serving the CSS/JS it cached before an update
    and the app looks broken until a hard refresh. Recomputed each start, which
    is exactly when the files can have changed.
    """
    times = [p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()]
    return str(int(max(times))) if times else "0"


templates.env.globals["static_v"] = _static_version()


@app.exception_handler(tmdb.TMDBError)
async def _tmdb_error(request: Request, exc: tmdb.TMDBError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(discord.DiscordError)
async def _discord_error(request: Request, exc: discord.DiscordError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ==========================================================================
# Pages
# ==========================================================================

def _page(request: Request, name: str, **ctx: Any):
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        name,
        {
            "settings": settings,
            "configured": bool(settings.get("tmdb_api_key")),
            "tmdb_key_url": TMDB_API_KEY_URL,
            **ctx,
        },
    )


def _release_is_over(release: dict[str, Any], now: int) -> bool:
    return int(release["drop_at"]) < now


def _showing_is_over(showing: dict[str, Any], now: int) -> bool:
    """Over once it has finished, not once it has started — a movie you are
    currently sitting in should still be on the dashboard."""
    ends = showing.get("end_at")
    if not ends:
        minutes = showing.get("runtime") or 120
        ends = int(showing["start_at"]) + minutes * 60
    return int(ends) < now


@app.get("/")
async def page_dashboard(request: Request):
    # Epoch comparison, so this is timezone-independent.
    now = int(time.time())

    releases = store.list_releases()
    showings = store.list_showings()

    upcoming_releases = [r for r in releases if not _release_is_over(r, now)]
    past_releases = [r for r in releases if _release_is_over(r, now)]
    upcoming_showings = [s for s in showings if not _showing_is_over(s, now)]
    past_showings = [s for s in showings if _showing_is_over(s, now)]

    # Soonest first for things still ahead; most recent first for things behind.
    upcoming_releases.sort(key=lambda r: r["drop_at"])
    upcoming_showings.sort(key=lambda s: s["start_at"])

    return _page(
        request,
        "dashboard.html",
        releases=upcoming_releases,
        showings=upcoming_showings,
        past_releases=past_releases,
        past_showings=past_showings,
    )


@app.get("/api/calendar/events")
async def api_calendar_events():
    """Flat event list for the dashboard month view.

    Times stay as epochs so the browser can bucket them into days in the
    viewer's own timezone rather than the Pi's.
    """
    events = [
        {
            "kind": "release", "id": r["id"], "title": r["title"],
            "at": r["drop_at"], "end": None,
            "poster_path": r["poster_path"], "href": "/releases",
        }
        for r in store.list_releases()
    ] + [
        {
            "kind": "showing", "id": s["id"], "title": s["title"],
            "at": s["start_at"], "end": s["end_at"],
            "poster_path": s["poster_path"], "href": "/upcoming",
        }
        for s in store.list_showings()
    ]
    events.sort(key=lambda e: e["at"])
    return {"events": events}


@app.get("/releases")
async def page_releases(request: Request):
    return _page(request, "releases.html", releases=store.list_releases())


@app.get("/upcoming")
async def page_upcoming(request: Request):
    return _page(
        request,
        "upcoming.html",
        showings=store.list_showings(),
        people=store.list_people(),
        theaters=store.list_theaters(),
    )


@app.get("/upcoming/{showing_id}/edit")
async def page_edit_showing(request: Request, showing_id: int):
    showing = store.get_showing(showing_id)
    if not showing:
        raise HTTPException(404, "Showing not found.")
    return _page(
        request,
        "edit_showing.html",
        showing=showing,
        people=store.list_people(),
        theaters=store.list_theaters(),
        attending={a["id"] for a in showing["attendees"] if not a["dropped"]},
        dropped={a["id"] for a in showing["attendees"] if a["dropped"]},
    )


@app.get("/releases/{release_id}/edit")
async def page_edit_release(request: Request, release_id: int):
    release = store.get_release(release_id)
    if not release:
        raise HTTPException(404, "Ticket release not found.")
    return _page(request, "edit_release.html", release=release)


@app.get("/people")
async def page_people(request: Request):
    return _page(
        request, "people.html", people=store.list_people(), theaters=store.list_theaters()
    )


@app.get("/settings")
async def page_settings(request: Request):
    return _page(request, "settings.html")


# ==========================================================================
# Calendar (.ics)
# ==========================================================================

def _ics_response(body: str, filename: str, *, download: bool) -> Response:
    # A subscribed feed must not be cached, or the calendar app keeps showing
    # times that have since been edited.
    disposition = "attachment" if download else "inline"
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-cache, must-revalidate",
        },
    )


def _safe_name(title: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in title).strip()
    return (keep or "event").replace(" ", "-")[:60]


@app.get("/calendar.ics", include_in_schema=False)
async def calendar_feed(showings: bool = True, releases: bool = True):
    """Whole-calendar feed, meant to be subscribed to rather than downloaded."""
    events = []
    if showings:
        events += [ics.showing_event(s) for s in store.list_showings()]
    if releases:
        events += [ics.release_event(r) for r in store.list_releases()]
    return _ics_response(ics.build(events), "movie-nights.ics", download=False)


@app.get("/upcoming/{showing_id}.ics", include_in_schema=False)
async def showing_ics(showing_id: int):
    showing = store.get_showing(showing_id)
    if not showing:
        raise HTTPException(404, "Showing not found.")
    body = ics.build([ics.showing_event(showing)], name=showing["title"])
    return _ics_response(body, f"{_safe_name(showing['title'])}.ics", download=True)


@app.get("/releases/{release_id}.ics", include_in_schema=False)
async def release_ics(release_id: int):
    release = store.get_release(release_id)
    if not release:
        raise HTTPException(404, "Ticket release not found.")
    body = ics.build([ics.release_event(release)], name=release["title"])
    return _ics_response(body, f"{_safe_name(release['title'])}-tickets.ics", download=True)


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Served from the root on purpose.

    A worker at /static/sw.js could only control /static/*; it has to be at the
    root to take scope over the whole app.
    """
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# ==========================================================================
# Settings API
# ==========================================================================

class SettingsIn(BaseModel):
    tmdb_api_key: str = ""
    tickets_webhook_url: str = ""
    tickets_is_thread: bool = False
    upcoming_use_primary: bool = True
    upcoming_webhook_url: str = ""
    upcoming_is_thread: bool = False
    auto_update: bool = True
    update_interval_hours: int = Field(default=6, ge=1, le=168)


@app.get("/api/settings")
async def api_get_settings():
    return get_settings()


@app.put("/api/settings")
async def api_put_settings(payload: SettingsIn):
    return save_settings(payload.model_dump())


@app.post("/api/settings/test-tmdb")
async def api_test_tmdb(payload: dict[str, str]):
    key = (payload.get("tmdb_api_key") or "").strip()
    if not key:
        raise HTTPException(400, "Enter a TMDB API key first.")
    if not await tmdb.verify_key(key):
        raise HTTPException(400, "TMDB rejected that key.")
    return {"ok": True, "message": "TMDB key works."}


@app.post("/api/settings/test-webhook/{kind}")
async def api_test_webhook(kind: str):
    if kind not in ("tickets", "upcoming"):
        raise HTTPException(404, "Unknown webhook.")
    await discord.post_test(kind)
    return {"ok": True, "message": f"Test message sent to the {kind} webhook."}


# ==========================================================================
# Updates
# ==========================================================================

@app.get("/api/update/check")
async def api_update_check():
    return await updater.check()


@app.post("/api/update/apply")
async def api_update_apply():
    result = await updater.apply()
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Update failed."))
    if result.get("restart_required"):
        if updater.under_systemd():
            updater.schedule_restart()
            result["restarting"] = True
        else:
            result["restarting"] = False
            result["message"] = (
                f"{result['message']} Restart the app manually to load the new code."
            )
    return result


# ==========================================================================
# TMDB API
# ==========================================================================

@app.get("/api/tmdb/search")
async def api_tmdb_search(q: str = ""):
    return {"results": await tmdb.search_movies(q)}


@app.get("/api/tmdb/movie/{tmdb_id}")
async def api_tmdb_movie(tmdb_id: int):
    return await tmdb.get_movie(tmdb_id)


@app.get("/api/trailer/find")
async def api_find_trailer(title: str, year: str | None = None):
    """Look a trailer up on demand, for the 'Find trailer' button."""
    if not title.strip():
        raise HTTPException(400, "Give a movie title to search for.")
    url = await trailers.find_trailer(title.strip(), year)
    return {
        "trailer_url": url,
        "query": trailers.search_query(title.strip(), year),
        "message": "Found a trailer." if url else "Couldn't find a trailer for that.",
    }


async def _resolve_trailer(pasted: str | None, movie: dict[str, Any]) -> str | None:
    """A hand-entered URL wins, but is canonicalised first.

    Discord only renders a player for a real youtube.com/watch link, so a share
    link, a shorts link, or a Google 'I'm feeling lucky' URL is followed and
    turned into one rather than posted as-is.
    """
    if pasted:
        return await trailers.normalize(pasted)
    return movie.get("trailer_url")


# ==========================================================================
# People & theaters API
# ==========================================================================

class PersonIn(BaseModel):
    name: str = Field(min_length=1)
    discord_id: str | None = None


class TheaterIn(BaseModel):
    name: str = Field(min_length=1)
    address: str = Field(min_length=1)


@app.get("/api/people")
async def api_list_people():
    return {"people": store.list_people()}


@app.post("/api/people", status_code=201)
async def api_create_person(payload: PersonIn):
    return store.create_person(payload.name, payload.discord_id)


@app.put("/api/people/{person_id}")
async def api_update_person(person_id: int, payload: PersonIn):
    store.update_person(person_id, payload.name, payload.discord_id)
    return {"ok": True}


@app.delete("/api/people/{person_id}")
async def api_delete_person(person_id: int):
    store.delete_person(person_id)
    return {"ok": True}


@app.get("/api/theaters")
async def api_list_theaters():
    return {"theaters": store.list_theaters()}


@app.post("/api/theaters", status_code=201)
async def api_create_theater(payload: TheaterIn):
    return store.create_theater(payload.name, payload.address)


@app.put("/api/theaters/{theater_id}")
async def api_update_theater(theater_id: int, payload: TheaterIn):
    store.update_theater(theater_id, payload.name, payload.address)
    return {"ok": True}


@app.delete("/api/theaters/{theater_id}")
async def api_delete_theater(theater_id: int):
    store.delete_theater(theater_id)
    return {"ok": True}


# ==========================================================================
# Ticket releases API
# ==========================================================================

class ReleaseIn(BaseModel):
    tmdb_id: int
    drop_at: int                    # unix epoch seconds, sent by the browser
    trailer_url: str | None = None  # overridable; otherwise pulled from TMDB
    remind: bool = True
    post_now: bool = True
    force_repost: bool = False


@app.get("/api/releases")
async def api_list_releases():
    return {"releases": store.list_releases()}


@app.post("/api/releases", status_code=201)
async def api_create_release(payload: ReleaseIn):
    movie = await tmdb.get_movie(payload.tmdb_id)
    record = {
        **movie,
        "drop_at": payload.drop_at,
        "remind": int(payload.remind),
        "trailer_url": await _resolve_trailer(payload.trailer_url, movie),
        "accent_color": await colors.average_color_from_url(
            poster_url(movie.get("poster_path"), "w185")
        ),
    }
    release = store.create_release(record)

    posted, warning = False, None
    if payload.post_now:
        try:
            thread = await discord.post_ticket_release(release)
            store.mark_release_posted(release["id"], thread)
            posted = True
        except discord.DiscordError as exc:
            warning = str(exc)

    return {"release": store.get_release(release["id"]), "posted": posted, "warning": warning}


@app.post("/api/releases/{release_id}/post")
async def api_post_release(release_id: int):
    release = store.get_release(release_id)
    if not release:
        raise HTTPException(404, "Ticket release not found.")
    thread = await discord.post_ticket_release(release)
    store.mark_release_posted(release_id, thread)
    return {"ok": True, "message": f"Posted “{release['title']}” to Discord."}


@app.get("/api/releases/{release_id}/preview")
async def api_preview_release(release_id: int):
    release = store.get_release(release_id)
    if not release:
        raise HTTPException(404, "Ticket release not found.")
    return {"embed": discord.build_ticket_release(release)}


@app.put("/api/releases/{release_id}")
async def api_update_release(release_id: int, payload: ReleaseIn):
    existing = store.get_release(release_id)
    if not existing:
        raise HTTPException(404, "Ticket release not found.")

    movie = await tmdb.get_movie(payload.tmdb_id)
    changed_movie = existing["tmdb_id"] != payload.tmdb_id
    updates = {
        **movie,
        "drop_at": payload.drop_at,
        "remind": int(payload.remind),
        "trailer_url": await _resolve_trailer(payload.trailer_url, movie),
        # Only re-derive the colour when the poster actually changed.
        "accent_color": (
            await colors.average_color_from_url(poster_url(movie.get("poster_path"), "w185"))
            if changed_movie or not existing.get("accent_color")
            else existing["accent_color"]
        ),
    }
    release = store.update_release(release_id, updates)
    repost = payload.force_repost or changed_movie

    synced, edited, reposted, warning = False, False, False, None
    if payload.post_now:
        try:
            result = await discord.sync_ticket_release(release, repost=repost)
            store.mark_release_posted(release_id, result)
            synced = True
            edited = result.get("edited", False)
            reposted = result.get("reposted", False)
            warning = result.get("warning")
        except discord.DiscordError as exc:
            warning = str(exc)

    return {
        "release": store.get_release(release_id),
        "synced": synced, "edited": edited, "reposted": reposted, "warning": warning,
    }


@app.delete("/api/releases/{release_id}")
async def api_delete_release(release_id: int):
    store.delete_release(release_id)
    return {"ok": True}


# ==========================================================================
# Showings (upcoming movies) API
# ==========================================================================

class ShowingIn(BaseModel):
    tmdb_id: int
    start_at: int
    end_at: int | None = None
    theater_id: int | None = None
    attendee_ids: list[int] = []
    extra_tickets: int = 0
    trailer_url: str | None = None
    remind: bool = True
    remind_hours: int = 1
    post_now: bool = True
    force_repost: bool = False


@app.get("/api/showings")
async def api_list_showings():
    return {"showings": store.list_showings()}


async def _showing_record(payload: ShowingIn) -> dict[str, Any]:
    movie = await tmdb.get_movie(payload.tmdb_id)
    runtime = movie.get("runtime")

    # No end time given? Derive it from the movie's runtime. An explicit end_at
    # from the form always wins, so this only ever fills a gap.
    end_at = payload.end_at
    if end_at is None and runtime:
        end_at = payload.start_at + runtime * 60

    return {
        "tmdb_id": movie["tmdb_id"],
        "title": movie["title"],
        "poster_path": movie["poster_path"],
        "backdrop_path": movie["backdrop_path"],
        "trailer_url": await _resolve_trailer(payload.trailer_url, movie),
        "accent_color": await colors.average_color_from_url(
            poster_url(movie.get("poster_path"), "w185")
        ),
        "runtime": runtime,
        "start_at": payload.start_at,
        "end_at": end_at,
        "theater_id": payload.theater_id,
        "extra_tickets": payload.extra_tickets,
        "remind": int(payload.remind),
        "remind_hours": payload.remind_hours,
    }


@app.post("/api/showings/preview")
async def api_preview_showing_draft(payload: ShowingIn):
    """Render the exact Discord message for an unsaved form, without storing anything."""
    theater = store.get_theater(payload.theater_id)
    draft = {
        **await _showing_record(payload),
        "attendees": store.people_by_ids(payload.attendee_ids),
        "theater_name": theater["name"] if theater else None,
        "theater_address": theater["address"] if theater else None,
    }
    return {"embed": discord.build_upcoming(draft)}


@app.post("/api/showings", status_code=201)
async def api_create_showing(payload: ShowingIn):
    showing = store.create_showing(await _showing_record(payload), payload.attendee_ids)

    posted, warning = False, None
    if payload.post_now:
        try:
            thread = await discord.post_upcoming(showing)
            store.mark_showing_posted(showing["id"], thread)
            posted = True
        except discord.DiscordError as exc:
            warning = str(exc)

    return {"showing": store.get_showing(showing["id"]), "posted": posted, "warning": warning}


@app.post("/api/showings/{showing_id}/post")
async def api_post_showing(showing_id: int):
    showing = store.get_showing(showing_id)
    if not showing:
        raise HTTPException(404, "Showing not found.")
    thread = await discord.post_upcoming(showing)
    store.mark_showing_posted(showing_id, thread)
    return {"ok": True, "message": f"Posted “{showing['title']}” to Discord."}


@app.get("/api/showings/{showing_id}/preview")
async def api_preview_showing(showing_id: int):
    showing = store.get_showing(showing_id)
    if not showing:
        raise HTTPException(404, "Showing not found.")
    return {"embed": discord.build_upcoming(showing)}


@app.put("/api/showings/{showing_id}")
async def api_update_showing(showing_id: int, payload: ShowingIn):
    existing = store.get_showing(showing_id)
    if not existing:
        raise HTTPException(404, "Showing not found.")

    record = await _showing_record(payload)
    if existing["tmdb_id"] == payload.tmdb_id and existing.get("accent_color"):
        record["accent_color"] = existing["accent_color"]  # poster unchanged, keep the colour

    showing = store.update_showing(showing_id, record, payload.attendee_ids)

    # Discord bakes a webhook message's name and avatar in at creation, so a
    # different movie needs a fresh post for those to match.
    repost = payload.force_repost or existing["tmdb_id"] != payload.tmdb_id

    synced, edited, reposted, warning = False, False, False, None
    if payload.post_now:
        try:
            result = await discord.sync_upcoming(showing, repost=repost)
            store.mark_showing_posted(showing_id, result)
            synced = True
            edited = result.get("edited", False)
            reposted = result.get("reposted", False)
            warning = result.get("warning")
        except discord.DiscordError as exc:
            warning = str(exc)

    return {
        "showing": store.get_showing(showing_id),
        "synced": synced, "edited": edited, "reposted": reposted, "warning": warning,
    }


@app.delete("/api/showings/{showing_id}")
async def api_delete_showing(showing_id: int):
    store.delete_showing(showing_id)
    return {"ok": True}
