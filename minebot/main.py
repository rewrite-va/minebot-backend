from __future__ import annotations

import asyncio
import logging
import os

from minebot.actions.registry import ActionRegistry
from minebot.bot.combat import CombatController, register_combat_actions
from minebot.bot.help import register_help_action
from minebot.bot.inventory import InventoryController, register_inventory_actions
from minebot.bot.inventory_announcer import InventoryAnnouncer
from minebot.bot.movement import MovementController, register_movement_actions
from minebot.bot.run_loop import run
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.observer import ObserverServer
from minebot.bridge.self_position import SelfPositionTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController
from minebot.logging_setup import configure_logging

log_path = configure_logging(os.environ.get("MINEBOT_LOG_LEVEL", "INFO"))
log = logging.getLogger("minebot")
log.info("logging to %s", log_path)


async def run_bot(config: BotConfig) -> None:
    observer = ObserverServer(config.observer_host, config.observer_port)
    await observer.start()

    bridge = ModBridge(config.mod_host, config.mod_port, observer)
    log.info("waiting for minebot-mod to connect on %s:%s", config.mod_host, config.mod_port)
    await bridge.connect()

    actions = ActionRegistry()
    tracker = EntityTracker()
    inventory = InventoryTracker()
    self_position = SelfPositionTracker()

    movement = MovementController(bridge, tracker)
    register_movement_actions(actions, movement)
    combat = CombatController(bridge, tracker)
    register_combat_actions(actions, combat)
    inventory_controller = InventoryController(bridge, inventory, tracker)
    register_inventory_actions(actions, inventory_controller)
    InventoryAnnouncer(bridge, inventory)  # registers itself as an inventory-change listener; not otherwise referenced
    register_help_action(actions)

    llm = LLMController(bridge, actions)  # no provider configured yet -- see llm/controller.py

    try:
        await run(bridge, actions, tracker, inventory, llm, config, self_position)
    finally:
        await bridge.close()
        await observer.close()


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
