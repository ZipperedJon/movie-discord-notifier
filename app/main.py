"""FastAPI app: pages, JSON API, and the background reminder loop."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import colors, discord, scheduler, store, tmdb
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


@app.get("/")
async def page_dashboard(request: Request):
    return _page(
        request,
        "dashboard.html",
        releases=store.list_releases(),
        showings=store.list_showings(),
    )


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


@app.get("/people")
async def page_people(request: Request):
    return _page(
        request, "people.html", people=store.list_people(), theaters=store.list_theaters()
    )


@app.get("/settings")
async def page_settings(request: Request):
    return _page(request, "settings.html")


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
# TMDB API
# ==========================================================================

@app.get("/api/tmdb/search")
async def api_tmdb_search(q: str = ""):
    return {"results": await tmdb.search_movies(q)}


@app.get("/api/tmdb/movie/{tmdb_id}")
async def api_tmdb_movie(tmdb_id: int):
    return await tmdb.get_movie(tmdb_id)


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
        "trailer_url": payload.trailer_url or movie.get("trailer_url"),
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


@app.get("/api/showings")
async def api_list_showings():
    return {"showings": store.list_showings()}


async def _showing_record(payload: ShowingIn) -> dict[str, Any]:
    movie = await tmdb.get_movie(payload.tmdb_id)
    return {
        "tmdb_id": movie["tmdb_id"],
        "title": movie["title"],
        "poster_path": movie["poster_path"],
        "backdrop_path": movie["backdrop_path"],
        "trailer_url": payload.trailer_url or movie.get("trailer_url"),
        "accent_color": await colors.average_color_from_url(
            poster_url(movie.get("poster_path"), "w185")
        ),
        "start_at": payload.start_at,
        "end_at": payload.end_at,
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


@app.delete("/api/showings/{showing_id}")
async def api_delete_showing(showing_id: int):
    store.delete_showing(showing_id)
    return {"ok": True}
