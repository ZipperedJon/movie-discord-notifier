"""Poster colour for the Discord embed's left sidebar.

Several ways to pick it, chosen in Settings:

  primary        the colour a person would name if asked what colour the poster
                 is — vivid, mid-lightness, and covering a decent area
  vibrant_light  the brightest vivid colour on the poster
  vibrant_dark   the deepest vivid colour on the poster
  muted          dominant but subdued, for a quieter bar
  dominant       simply the colour covering the most area, unfiltered — often
                 the dark background, which is honest but usually drab
  average        the mean of every pixel; almost always a muddy brown or grey,
                 because posters are mostly dark background
  fixed          no extraction, just Discord blurple

Everything except `average` and `fixed` works from a quantised palette and
scores each entry on area, vividness and how close its lightness is to what
that mode is after. Area alone would return the dark background every time.
"""

import colorsys
import io
import logging

import httpx
from PIL import Image, ImageStat

log = logging.getLogger("colors")

FALLBACK = 0x5865F2  # Discord blurple

# Bump when the maths changes, so stored colours get recomputed.
ALGO = "v2"

DEFAULT_MODE = "primary"

# label, and a one-line description for the settings page
MODES: dict[str, tuple[str, str]] = {
    "primary": ("Primary", "The poster's main colour. Vivid and balanced."),
    "vibrant_light": ("Light vibrant", "The brightest bold colour on the poster."),
    "vibrant_dark": ("Dark vibrant", "The deepest bold colour on the poster."),
    "muted": ("Muted", "Dominant but subdued — a quieter bar."),
    "dominant": ("Most common", "Whatever covers the most area, unfiltered."),
    "average": ("Average", "Mean of every pixel. Usually muddy."),
    "fixed": ("Discord blurple", "No extraction — always #5865F2."),
}

PALETTE_SIZE = 24
MIN_VALUE = 0.12          # below this is effectively black
MAX_VALUE = 0.98          # above this is blown-out white
MIN_OUTPUT_VALUE = 0.28   # lift near-black winners so the bar is visible

# target lightness, and whether the mode wants saturation high or low
_TARGETS: dict[str, tuple[float, str]] = {
    "primary": (0.62, "high"),
    "vibrant_light": (0.82, "high"),
    "vibrant_dark": (0.30, "high"),
    "muted": (0.55, "low"),
}


def _to_int(rgb) -> int:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return (r << 16) + (g << 8) + b


def to_hex(value: int | None) -> str:
    return f"#{(value if value is not None else FALLBACK):06X}"


def _score(count: int, total: int, rgb, target_value: float, sat_pref: str) -> float:
    _, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    population = count / total

    if v < MIN_VALUE:
        return -1.0
    # Only reject the top end when it is genuinely washed out. A brilliant
    # saturated yellow or orange also has value ~1.0, and excluding those on
    # brightness alone threw away exactly the colours these modes want.
    if v > MAX_VALUE and s < 0.15:
        return -1.0

    if sat_pref == "high":
        if s < 0.12:                      # a flat grey says nothing about the poster
            return population * 0.05
        vividness = 0.25 + 0.75 * s
    else:                                  # muted: some colour, but restrained
        if s < 0.06:
            return population * 0.05
        vividness = 0.25 + 0.75 * (1 - min(s, 1.0))

    # Square root damps raw area so a large dull region cannot always outrank a
    # smaller, far more characteristic colour.
    area = population ** 0.5
    lightness = max(1 - abs(v - target_value) / max(target_value, 1 - target_value), 0.12)
    return area * vividness * lightness


def _lift(rgb):
    """Raise a near-black winner just enough to read as a coloured bar."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if v >= MIN_OUTPUT_VALUE:
        return rgb
    r, g, b = colorsys.hsv_to_rgb(h, s, MIN_OUTPUT_VALUE)
    return (r * 255, g * 255, b * 255)


def _palette(data: bytes):
    """(count, rgb) pairs for a quantised version of the image."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        # Small enough to be quick, large enough to keep real colour regions.
        img.thumbnail((200, 200))
        quantised = img.quantize(colors=PALETTE_SIZE, method=Image.Quantize.MEDIANCUT)
        palette = quantised.getpalette() or []
        entries = []
        for count, index in (quantised.getcolors() or []):
            rgb = tuple(palette[index * 3: index * 3 + 3])
            if len(rgb) == 3:
                entries.append((count, rgb))
        return entries


def average_color_bytes(data: bytes) -> int | None:
    """Mean RGB. Also the last resort when quantising finds nothing usable."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((128, 128))
            mean = ImageStat.Stat(img).mean
            return _to_int((mean[0], mean[1], mean[2]))
    except Exception:
        log.warning("Could not read poster image for colour extraction", exc_info=True)
        return None


def extract(data: bytes, mode: str = DEFAULT_MODE) -> int | None:
    """The poster's colour under `mode`. Never raises."""
    if mode == "fixed":
        return FALLBACK
    if mode == "average":
        return average_color_bytes(data)

    try:
        entries = _palette(data)
        if not entries:
            return average_color_bytes(data)

        if mode == "dominant":
            return _to_int(_lift(max(entries, key=lambda e: e[0])[1]))

        target_value, sat_pref = _TARGETS.get(mode, _TARGETS[DEFAULT_MODE])
        total = sum(count for count, _ in entries)

        best_score, best_rgb = 0.0, None
        for count, rgb in entries:
            score = _score(count, total, rgb, target_value, sat_pref)
            if score > best_score:
                best_score, best_rgb = score, rgb

        # Nothing vivid enough anywhere: fall back to sheer area.
        chosen = best_rgb or max(entries, key=lambda e: e[0])[1]
        return _to_int(_lift(chosen))
    except Exception:
        log.warning("Could not read poster image for colour extraction", exc_info=True)
        return None


async def fetch_poster(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        return resp.content if resp.status_code == 200 else None
    except httpx.HTTPError:
        log.warning("Could not fetch poster for colour extraction: %s", url)
        return None


async def poster_color_from_url(url: str | None, mode: str = DEFAULT_MODE) -> int | None:
    data = await fetch_poster(url)
    return extract(data, mode) if data else None


async def all_modes_from_url(url: str | None) -> dict[str, int | None]:
    """Every mode for one poster, from a single download — for the preview."""
    data = await fetch_poster(url)
    if not data:
        return {}
    return {mode: extract(data, mode) for mode in MODES}
