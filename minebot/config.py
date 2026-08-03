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

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()  # loads .env into os.environ if present; no-op otherwise
        return cls(
            mod_host=os.environ.get("MINEBOT_MOD_HOST", "0.0.0.0"),
            mod_port=int(os.environ.get("MINEBOT_MOD_PORT", "47893")),
            bot_name=os.environ.get("MINEBOT_BOT_NAME", "minebot"),
        )
