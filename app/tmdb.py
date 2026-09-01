"""Thin TMDB client. The key comes from the settings table, never from source."""

from typing import Any

import httpx

from . import trailers
from .config import TMDB_API_BASE, poster_url
from .db import get_settings


class TMDBError(RuntimeError):
    pass


def _api_key() -> str:
    key = (get_settings().get("tmdb_api_key") or "").strip()
    if not key:
        raise TMDBError("No TMDB API key saved yet. Add one on the Settings page.")
    return key


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    key = _api_key()
    query = {"language": "en-US", **(params or {})}
    headers = {"accept": "application/json"}

    # TMDB issues two credential styles. A v4 read token is a JWT and goes in the
    # Authorization header; a v3 key goes in the query string. Support both so the
    # user can paste whichever their TMDB settings page shows them.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        query["api_key"] = key

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{TMDB_API_BASE}{path}", params=query, headers=headers)

    if resp.status_code == 401:
        raise TMDBError("TMDB rejected the API key (401). Check it on the Settings page.")
    if resp.status_code == 404:
        raise TMDBError("TMDB has no record for that request (404).")
    if resp.status_code >= 400:
        raise TMDBError(f"TMDB error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def search_movies(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Live-search results shaped for the picker: poster, title, year."""
    if not query.strip():
        return []
    data = await _get("/search/movie", {"query": query, "include_adult": "false"})
    results = []
    for movie in data.get("results", [])[:limit]:
        date = movie.get("release_date") or ""
        results.append(
            {
                "tmdb_id": movie["id"],
                "title": movie.get("title") or movie.get("original_title") or "Untitled",
                "year": date[:4] or None,
                "release_date": date or None,
                "overview": movie.get("overview") or "",
                "poster_path": movie.get("poster_path"),
                "backdrop_path": movie.get("backdrop_path"),
                "poster_url": poster_url(movie.get("poster_path"), "w185"),
            }
        )
    return results


def _pick_trailer(videos: dict[str, Any]) -> str | None:
    """Prefer an official YouTube 'Trailer', then any trailer, then any teaser."""
    clips = [v for v in videos.get("results", []) if v.get("site") == "YouTube" and v.get("key")]

    def rank(clip: dict[str, Any]) -> tuple[int, int]:
        kind = (clip.get("type") or "").lower()
        type_rank = {"trailer": 0, "teaser": 1}.get(kind, 2)
        return (type_rank, 0 if clip.get("official") else 1)

    if not clips:
        return None
    best = sorted(clips, key=rank)[0]
    return f"https://www.youtube.com/watch?v={best['key']}"


async def get_movie(tmdb_id: int, *, find_trailer: bool = True) -> dict[str, Any]:
    """Full detail block used to build the Discord posts.

    TMDB is missing a trailer for plenty of older or smaller films. When it is,
    fall back to searching for one so the post still gets a player.
    """
    data = await _get(f"/movie/{tmdb_id}", {"append_to_response": "videos"})
    title = data.get("title") or data.get("original_title") or "Untitled"
    release_date = data.get("release_date") or None

    trailer_url = _pick_trailer(data.get("videos") or {})
    trailer_source = "tmdb" if trailer_url else None
    if not trailer_url and find_trailer:
        trailer_url = await trailers.find_trailer(title, (release_date or "")[:4] or None)
        trailer_source = "search" if trailer_url else None

    return {
        "tmdb_id": data["id"],
        "title": title,
        "tagline": data.get("tagline") or None,
        "overview": data.get("overview") or None,
        "release_date": release_date,
        "genres": ", ".join(g["name"] for g in data.get("genres", [])) or None,
        "studios": ", ".join(c["name"] for c in data.get("production_companies", [])) or None,
        "budget": data.get("budget") or None,
        "runtime": data.get("runtime") or None,
        "poster_path": data.get("poster_path"),
        "backdrop_path": data.get("backdrop_path"),
        "trailer_url": trailer_url,
        "trailer_source": trailer_source,
        "homepage": f"https://www.themoviedb.org/movie/{data['id']}",
    }


async def verify_key(key: str) -> bool:
    """Used by the Settings page 'Test' button."""
    headers = {"accept": "application/json"}
    params: dict[str, Any] = {}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{TMDB_API_BASE}/configuration", params=params, headers=headers
        )
    return resp.status_code == 200
