"""Average poster colour, used as the Discord embed's left sidebar colour."""

import io
import logging

import httpx
from PIL import Image, ImageStat

log = logging.getLogger("colors")

FALLBACK = 0x5865F2  # Discord blurple


def _to_int(rgb: tuple[int, int, int]) -> int:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return (r << 16) + (g << 8) + b


def average_color_bytes(data: bytes) -> int | None:
    """Mean RGB of the image, computed on a thumbnail so it stays fast."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            # Downscale first: the mean is identical in aggregate but far cheaper,
            # and it smooths out single-pixel noise.
            img.thumbnail((128, 128))
            mean = ImageStat.Stat(img).mean
            return _to_int((mean[0], mean[1], mean[2]))
    except Exception:
        log.warning("Could not read poster image for colour extraction", exc_info=True)
        return None


async def average_color_from_url(url: str | None) -> int | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return average_color_bytes(resp.content)
    except httpx.HTTPError:
        log.warning("Could not fetch poster for colour extraction: %s", url)
        return None
