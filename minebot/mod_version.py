"""Compares the connected mod's reported build (see the `hello` event,
sourced from minebot-mod's BuildInfo) against the mod repo's own current
git commit, to catch a stale-deployed-but-not-yet-restarted client --
otherwise indistinguishable, from the backend's logs alone, from "the fix
doesn't actually work" (this bit a real debugging session more than once:
a jar was rebuilt and redeployed with a real fix, but the running game
client hadn't been restarted, so it kept running the old code with no
error or obvious signal anywhere).
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone

log = logging.getLogger("minebot.mod_version")


def _format_built_at(built_at: str) -> str:
    """Renders BuildInfo's ISO-8601 built_at ("2026-08-05T05:39:30.928Z")
    as "v20260805 05.39.30" -- easier to read at a glance in the connect
    log line than a raw ISO timestamp, while still sorting/comparing
    naturally since it keeps the same year-month-day ordering. Falls back
    to the raw string on anything unparseable (a malformed/placeholder
    build info shouldn't crash the connect handshake over a display
    nicety).
    """
    try:
        parsed = datetime.fromisoformat(built_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return built_at
    return parsed.strftime("v%Y%m%d %H.%M.%S")


def expected_commit(mod_repo_path: str | None) -> str | None:
    if mod_repo_path is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=mod_repo_path, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("couldn't read minebot-mod's current commit at %s: %s", mod_repo_path, e)
        return None
    if result.returncode != 0:
        log.warning("git rev-parse failed in %s: %s", mod_repo_path, result.stderr.strip())
        return None
    return result.stdout.strip()


def check_hello(reported_commit: str, built_at: str, mod_repo_path: str | None) -> None:
    log.info("backend: connected (%s, commit=%s)", _format_built_at(built_at), reported_commit)

    expected = expected_commit(mod_repo_path)
    if expected is None:
        return

    reported_base = reported_commit.removesuffix("-dirty")
    if reported_base == expected:
        return

    log.warning(
        "minebot-mod's connected build (commit=%s) does not match this machine's "
        "current minebot-mod checkout (commit=%s) -- the running game client is likely "
        "still on a stale/undeployed build. Rebuild, redeploy the jar, and fully restart "
        "the game client (see AGENTS.md).",
        reported_commit, expected,
    )
