from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    # Address to bind the control-channel WebSocket server to (Python is
    # the server, the mod connects out to it -- see bridge/client.py's
    # ModBridge docstring for why). "0.0.0.0" (all interfaces) is the
    # default since WSL2's own 127.0.0.1 is what the mod's "localhost"
    # connection actually resolves to via WSL2's localhost-forwarding, and
    # binding only to 127.0.0.1 already covers that case, but 0.0.0.0
    # keeps this working with less networking-specific tinkering.
    mod_host: str
    mod_port: int

    # The bot's own in-game username -- used to decide whether a chat
    # message is addressed to the bot (see llm/trigger.py's
    # should_trigger_llm), since the control channel has no other signal
    # for "this message is meant for you" (no whisper/DM flag is
    # currently forwarded by the mod, see FINDINGS.md's known gaps).
    bot_name: str

    # Additional words/phrases that also trigger an LLM call, beyond the
    # bot's own name -- e.g. a nickname the bot is known by, or a
    # catch-all like "hey bot". Matched the same way (case-insensitive
    # substring), same as bot_name.
    trigger_words: tuple[str, ...]

    # Local path to the minebot-mod repo checkout, used only to compare
    # its current `git rev-parse HEAD` against the commit the connected
    # mod reports in its `hello` event (see run_loop.py) -- catches a
    # stale-deployed-jar-that-was-never-restarted, which otherwise looks
    # identical to "the fix doesn't work" from the backend's logs alone
    # (this bit a real debugging session more than once). None disables
    # the check entirely (e.g. running somewhere this sibling checkout
    # doesn't exist).
    mod_repo_path: str | None

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()  # loads .env into os.environ if present; no-op otherwise
        return cls(
            mod_host=os.environ.get("MINEBOT_MOD_HOST", "0.0.0.0"),
            mod_port=int(os.environ.get("MINEBOT_MOD_PORT", "47893")),
            bot_name=os.environ.get("MINEBOT_BOT_NAME", "minebot"),
            trigger_words=_parse_trigger_words(os.environ.get("MINEBOT_TRIGGER_WORDS", "")),
            mod_repo_path=os.environ.get("MINEBOT_MOD_REPO_PATH", "/home/colaila/git/mods/minebot-mod"),
        )


def _parse_trigger_words(raw: str) -> tuple[str, ...]:
    return tuple(word.strip() for word in raw.split(",") if word.strip())
