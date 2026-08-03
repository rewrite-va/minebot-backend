from __future__ import annotations

import asyncio
import logging
import os

from minebot.actions.registry import ActionRegistry
from minebot.bot.inventory import InventoryController, register_inventory_actions
from minebot.bot.movement import MovementController, register_movement_actions
from minebot.bot.run_loop import run
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController
from minebot.logging_setup import configure_logging

log_path = configure_logging(os.environ.get("MINEBOT_LOG_LEVEL", "INFO"))
log = logging.getLogger("minebot")
log.info("logging to %s", log_path)


async def run_bot(config: BotConfig) -> None:
    bridge = ModBridge(config.mod_host, config.mod_port)
    log.info("waiting for minebot-mod to connect on %s:%s", config.mod_host, config.mod_port)
    await bridge.connect()

    actions = ActionRegistry()
    tracker = EntityTracker()
    inventory = InventoryTracker()

    movement = MovementController(bridge, tracker)
    register_movement_actions(actions, movement)
    inventory_controller = InventoryController(bridge, inventory, tracker)
    register_inventory_actions(actions, inventory_controller)

    llm = LLMController(bridge, actions)  # no provider configured yet -- see llm/controller.py

    try:
        await run(bridge, actions, tracker, inventory, llm, config, movement)
    finally:
        await bridge.close()


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
