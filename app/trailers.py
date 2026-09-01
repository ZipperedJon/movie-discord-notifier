"""Find a trailer when TMDB doesn't have one.

Whatever route is used, the result is always a canonical
https://www.youtube.com/watch?v=<id> URL — Discord only renders a player for a
real YouTube link, so posting a search or redirect URL would just show a link.

Order:
  1. YouTube's own search results page. No API key, and it answers a server
     honestly rather than showing it a bot wall.
  2. Google "I'm Feeling Lucky". Kept as a backstop, but Google frequently
     serves automated clients a consent page or CAPTCHA instead of the
     redirect, so it is second rather than first.
"""

import logging
import re
from urllib.parse import quote_plus, urlparse, parse_qs

import httpx

log = logging.getLogger("trailers")

YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query={q}"
GOOGLE_LUCKY = "https://www.google.com/search?q={q}&btnI=1"

# A YouTube video id is exactly 11 chars of [A-Za-z0-9_-].
_VIDEO_ID = r"[A-Za-z0-9_-]{11}"
_IN_PAGE = re.compile(rf'"videoId":"({_VIDEO_ID})"')
_IN_HREF = re.compile(rf'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)({_VIDEO_ID})')

# Without a desktop UA YouTube serves a stripped page with no ytInitialData,
# and the CONSENT cookie skips the EU interstitial that hides the results.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_COOKIES = {"CONSENT": "YES+1"}


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_video_id(url: str | None) -> str | None:
    """Pull the video id out of any YouTube URL shape."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower().removeprefix("www.").removeprefix("m.")

    if host in ("youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
            if candidate and re.fullmatch(_VIDEO_ID, candidate):
                return candidate
        for prefix in ("/embed/", "/shorts/", "/v/", "/live/"):
            if parsed.path.startswith(prefix):
                candidate = parsed.path[len(prefix):].split("/")[0]
                if re.fullmatch(_VIDEO_ID, candidate):
                    return candidate
    elif host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        if re.fullmatch(_VIDEO_ID, candidate):
            return candidate
    return None


def search_query(title: str, year: int | str | None = None) -> str:
    return f"{title} ({year}) official trailer" if year else f"{title} official trailer"


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("Trailer lookup request failed for %s: %s", url, exc)
        return None


async def _from_youtube(client: httpx.AsyncClient, query: str) -> str | None:
    resp = await _fetch(client, YOUTUBE_SEARCH.format(q=quote_plus(query)))
    if not resp or resp.status_code != 200:
        return None
    match = _IN_PAGE.search(resp.text) or _IN_HREF.search(resp.text)
    return match.group(1) if match else None


async def _from_google_lucky(client: httpx.AsyncClient, query: str) -> str | None:
    """Follow the lucky redirect. Only a YouTube destination counts."""
    resp = await _fetch(client, GOOGLE_LUCKY.format(q=quote_plus(query)))
    if not resp:
        return None

    # The redirect usually lands straight on the video.
    landed = extract_video_id(str(resp.url))
    if landed:
        return landed

    # Otherwise this is probably a results or consent page — take the first
    # YouTube link in the body, if there is one.
    if resp.status_code == 200:
        match = _IN_HREF.search(resp.text)
        if match:
            return match.group(1)
    log.info("Google lucky lookup did not reach YouTube (status %s)", resp.status_code)
    return None


async def find_trailer(title: str, year: int | str | None = None) -> str | None:
    """Best-effort trailer URL for a movie, or None. Never raises."""
    query = search_query(title, year)
    async with httpx.AsyncClient(
        timeout=12, follow_redirects=True, headers=_HEADERS, cookies=_COOKIES
    ) as client:
        for source, finder in (("youtube", _from_youtube), ("google", _from_google_lucky)):
            try:
                video_id = await finder(client, query)
            except Exception:
                log.warning("Trailer lookup via %s failed", source, exc_info=True)
                continue
            if video_id:
                log.info("Found trailer for %r via %s", query, source)
                return watch_url(video_id)

    log.info("No trailer found for %r", query)
    return None


async def normalize(url: str | None, *, resolve: bool = True) -> str | None:
    """Turn whatever the user pasted into a canonical YouTube watch URL.

    A share link, a shorts link or a timestamped URL becomes watch?v=<id>. A
    Google/lucky search URL is followed so Discord gets the video rather than a
    link it cannot render. Anything else is passed through untouched.
    """
    if not url:
        return None
    url = url.strip()

    video_id = extract_video_id(url)
    if video_id:
        return watch_url(video_id)

    host = (urlparse(url).netloc or "").lower()
    if resolve and "google." in host:
        async with httpx.AsyncClient(
            timeout=12, follow_redirects=True, headers=_HEADERS, cookies=_COOKIES
        ) as client:
            resp = await _fetch(client, url)
            if resp:
                landed = extract_video_id(str(resp.url))
                if landed:
                    return watch_url(landed)
                if resp.status_code == 200:
                    match = _IN_HREF.search(resp.text)
                    if match:
                        return watch_url(match.group(1))
    return url
