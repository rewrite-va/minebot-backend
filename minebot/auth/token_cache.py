"""On-disk cache for the MSA refresh token, so the bot doesn't need a fresh
device-code sign-in every run (matching how real Minecraft launchers behave
-- sign in once, then silently refresh).

Deliberately only caches the MSA refresh token, not the downstream
Xbox/XSTS/Minecraft tokens: those are cheap to re-derive (a handful of HTTP
calls, well under a second) each run from a valid MSA access token, and
caching them too would mean tracking multiple independent expiry clocks for
little benefit. The MSA refresh token is the one credential that's actually
expensive to replace (it requires an interactive sign-in), so it's the only
one worth persisting.

Stored as plain JSON on disk -- this is the same trust model prismarine-auth
and every MC launcher use (a refresh token is bearer-auth, like a session
cookie); protecting it further (OS keychain, encryption at rest) would be a
real improvement but is out of scope for this bot's threat model. The file
is created with owner-only permissions (0600) as a basic precaution.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

DEFAULT_CACHE_PATH = Path.home() / ".cache" / "minebot" / "msa_token.json"


def load_refresh_token(cache_path: Path = DEFAULT_CACHE_PATH) -> str | None:
    try:
        data = json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data.get("refresh_token")


def save_refresh_token(refresh_token: str, cache_path: Path = DEFAULT_CACHE_PATH) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"refresh_token": refresh_token}))
    os.chmod(cache_path, stat.S_IRUSR | stat.S_IWUSR)


def clear(cache_path: Path = DEFAULT_CACHE_PATH) -> None:
    cache_path.unlink(missing_ok=True)
