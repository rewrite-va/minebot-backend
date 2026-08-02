from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BotConfig:
    host: str
    port: int
    username: str
    online_mode: bool

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            host=os.environ.get("MINEBOT_HOST", "127.0.0.1"),
            port=int(os.environ.get("MINEBOT_PORT", "25565")),
            username=os.environ.get("MINEBOT_USERNAME", "minebot"),
            online_mode=os.environ.get("MINEBOT_ONLINE_MODE", "false").lower() == "true",
        )
