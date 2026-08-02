from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    mod_host: str
    mod_port: int

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()  # loads .env into os.environ if present; no-op otherwise
        return cls(
            mod_host=os.environ.get("MINEBOT_MOD_HOST", "127.0.0.1"),
            mod_port=int(os.environ.get("MINEBOT_MOD_PORT", "47893")),
        )
