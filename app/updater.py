"""Self-update from GitHub.

Checks the upstream branch for new commits, and can pull them in and restart.
Updating needs the install to be a git checkout whose files the service user can
write — which is what install.sh sets up.
"""

import asyncio
import logging
import os
import subprocess
import sys
from typing import Any

import httpx

from .config import BASE_DIR
from .db import get_settings, save_settings

log = logging.getLogger("updater")

REPO = os.environ.get("MDN_REPO", "ZipperedJon/movie-discord-notifier")
BRANCH = os.environ.get("MDN_BRANCH", "main")
GITHUB_API = "https://api.github.com"


def _run(*args: str, timeout: int = 300) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def is_git_checkout() -> bool:
    return (BASE_DIR / ".git").exists()


def local_commit() -> str | None:
    ok, out = _run("git", "rev-parse", "HEAD", timeout=15)
    return out.strip() if ok and out.strip() else None


def _venv_pip() -> str:
    """pip inside the venv we are running from, so updates land in the right place."""
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    candidate = BASE_DIR / ".venv" / bin_dir / ("pip.exe" if os.name == "nt" else "pip")
    return str(candidate) if candidate.exists() else ""


async def check() -> dict[str, Any]:
    """Compare the local commit with the newest one upstream. Never raises."""
    current = local_commit()
    result: dict[str, Any] = {
        "current": current,
        "current_short": current[:7] if current else None,
        "latest": None,
        "latest_short": None,
        "behind": False,
        "can_update": is_git_checkout(),
        "message": None,
        "subject": None,
        "checked_at": None,
    }

    if not is_git_checkout():
        result["message"] = (
            "This copy is not a git checkout, so it cannot update itself. "
            "Reinstall with install.sh to enable updates."
        )
        return result

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{REPO}/commits/{BRANCH}",
                headers={"Accept": "application/vnd.github+json"},
            )
        if resp.status_code != 200:
            result["message"] = f"GitHub returned {resp.status_code} when checking for updates."
            return result
        data = resp.json()
    except httpx.HTTPError as exc:
        result["message"] = f"Could not reach GitHub: {exc}"
        return result

    latest = data.get("sha")
    result["latest"] = latest
    result["latest_short"] = latest[:7] if latest else None
    result["subject"] = (data.get("commit", {}).get("message") or "").split("\n")[0][:120]
    result["behind"] = bool(current and latest and current != latest)
    result["message"] = (
        "An update is available." if result["behind"] else "You are on the latest version."
    )

    stamp = data.get("commit", {}).get("committer", {}).get("date")
    result["checked_at"] = stamp
    save_settings({"last_update_check": stamp or ""})
    return result


async def apply() -> dict[str, Any]:
    """Fetch, hard-reset to upstream, reinstall dependencies.

    Returns restart_required so the caller can decide when to exit — the process
    has to restart for the new code to take effect.
    """
    if not is_git_checkout():
        return {"ok": False, "message": "Not a git checkout — cannot update in place."}

    before = local_commit()

    ok, out = await asyncio.to_thread(_run, "git", "fetch", "--quiet", "origin", BRANCH)
    if not ok:
        return {"ok": False, "message": f"git fetch failed: {out[:300]}"}

    ok, out = await asyncio.to_thread(
        _run, "git", "reset", "--hard", "--quiet", f"origin/{BRANCH}"
    )
    if not ok:
        return {"ok": False, "message": f"git reset failed: {out[:300]}"}

    after = local_commit()
    if before == after:
        return {"ok": True, "updated": False, "message": "Already up to date.",
                "restart_required": False}

    pip = _venv_pip()
    if pip:
        ok, out = await asyncio.to_thread(
            _run, pip, "install", "--quiet", "-r", str(BASE_DIR / "requirements.txt")
        )
        if not ok:
            # Code is already updated, so say so rather than pretending it failed.
            log.warning("Dependency install failed after update: %s", out[:300])
            return {
                "ok": True, "updated": True, "restart_required": True,
                "message": f"Updated to {after[:7]}, but installing dependencies failed. "
                           f"Check the logs. ({out[:150]})",
            }

    log.info("Updated %s -> %s", (before or "?")[:7], (after or "?")[:7])
    return {
        "ok": True,
        "updated": True,
        "restart_required": True,
        "message": f"Updated to {after[:7]}. Restarting…",
    }


def schedule_restart(delay: float = 1.5) -> None:
    """Exit shortly, so the HTTP response is flushed first.

    systemd restarts us (the unit sets Restart=always). Outside systemd the
    process simply stops and needs starting again by hand.
    """
    async def _bye() -> None:
        await asyncio.sleep(delay)
        log.info("Restarting to load the update")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    asyncio.create_task(_bye())


def under_systemd() -> bool:
    return bool(os.environ.get("INVOCATION_ID")) or os.path.isdir("/run/systemd/system")


async def auto_update_once() -> None:
    """Background pass: check, and apply if the user enabled auto-update."""
    settings = get_settings()
    if not settings.get("auto_update"):
        return

    status = await check()
    if not status.get("behind"):
        return

    log.info("Update available (%s), applying", status.get("latest_short"))
    result = await apply()
    if result.get("restart_required"):
        if under_systemd():
            schedule_restart(delay=1.0)
        else:
            log.warning("Update installed. Restart the app to load it.")
