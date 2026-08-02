from __future__ import annotations

import asyncio
import logging
import os

from minebot.bot.movement import MovementController, register_movement_commands
from minebot.bot.run_loop import run
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.commands.registry import CommandRegistry
from minebot.config import BotConfig

logging.basicConfig(level=os.environ.get("MINEBOT_LOG_LEVEL", "INFO").upper())
log = logging.getLogger("minebot")


async def run_bot(config: BotConfig) -> None:
    bridge = ModBridge(config.mod_host, config.mod_port)
    log.info("waiting for minebot-mod to connect on %s:%s", config.mod_host, config.mod_port)
    await bridge.connect()

    registry = CommandRegistry()
    tracker = EntityTracker()
    movement = MovementController(bridge, tracker)
    register_movement_commands(registry, movement)

    try:
        await run(bridge, registry, tracker)
    finally:
        await bridge.close()


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
