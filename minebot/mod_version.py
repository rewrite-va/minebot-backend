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

log = logging.getLogger("minebot.mod_version")


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
    log.info("minebot-mod connected: commit=%s built_at=%s", reported_commit, built_at)

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
