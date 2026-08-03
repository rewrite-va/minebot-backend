"""Inventory actions -- inventory/equip/drop/give, mirroring movement.py's
shape: chat-command-to-command translation only, all the real work
(container clicks, drop actions, walking to a recipient) happens inside
the mod. Items are named by bare item id (e.g. "bread"), normalized to a
full "minecraft:<id>" registry id to match what InventoryTracker's
snapshots (and minebot-mod's InventoryReporter) use -- already-namespaced
ids ("minecraft:bread", or a future mod's own "othermod:thing") pass
through unchanged.
"""

from __future__ import annotations

import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker

log = logging.getLogger("minebot.inventory")

GIVE_STOP_DISTANCE = 2.0


def _normalize_item_id(item: str) -> str:
    return item if ":" in item else f"minecraft:{item}"


class InventoryController:
    def __init__(self, bridge: ModBridge, inventory: InventoryTracker, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.inventory = inventory
        self.tracker = tracker

    async def inventory_report(self, sender: str | None) -> ActionResult:
        slots = self.inventory.slots()
        if not slots:
            return ActionResult(message="my inventory is empty")
        summary = ", ".join(f"{s.count}x {s.item.removeprefix('minecraft:')}" for s in slots)
        return ActionResult(message=f"inventory: {summary}")

    async def equip(self, sender: str | None, item: str) -> ActionResult:
        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("equip: not carrying any %s", item)
            return ActionResult(message=f"I don't have any {item}")
        await self.bridge.send_equip(entry.slot)
        return ActionResult(message=f"ok, equipped {item}")

    async def drop(self, sender: str | None, item: str, count: float = 1) -> ActionResult:
        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("drop: not carrying any %s", item)
            return ActionResult(message=f"I don't have any {item}")
        await self.bridge.send_drop(entry.slot, int(count))
        return ActionResult(message=f"ok, dropped {int(count)}x {item}")

    async def give(self, sender: str | None, player_name: str, item: str, count: float = 1) -> ActionResult:
        entity = self.tracker.find_by_name(player_name)
        if entity is None:
            log.warning("give: no known entity named %s (not currently visible?)", player_name)
            return ActionResult(message=f"I can't see {player_name}")

        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("give: not carrying any %s", item)
            return ActionResult(message=f"I don't have any {item}")

        log.info("giving %dx %s to %s (entity %d)", int(count), item, player_name, entity.id)
        await self.bridge.send_give(entity.id, entry.slot, int(count), stop_distance=GIVE_STOP_DISTANCE)
        return ActionResult(message=f"ok, bringing {int(count)}x {item} to {player_name}")


def register_inventory_actions(registry: ActionRegistry, inventory: InventoryController) -> None:
    registry.register(Action(
        name="inventory",
        description="Report what items the bot is currently carrying.",
        handler=inventory.inventory_report,
    ))
    registry.register(Action(
        name="equip",
        description="Equip an item from inventory (armor/offhand) using the slot it best fits automatically.",
        handler=inventory.equip,
        params=[
            ActionParam("item", "string", "Item id to equip, e.g. 'diamond_chestplate'."),
        ],
    ))
    registry.register(Action(
        name="drop",
        description="Drop items from inventory onto the ground at the bot's current position.",
        handler=inventory.drop,
        params=[
            ActionParam("item", "string", "Item id to drop, e.g. 'cobblestone'."),
            ActionParam("count", "int", "How many to drop.", required=False),
        ],
    ))
    registry.register(Action(
        name="give",
        description="Walk to a player and drop items for them -- the closest real-world equivalent of handing them over, since there's no direct player-to-player transfer.",
        handler=inventory.give,
        params=[
            ActionParam("player_name", "string", "Name of the player to give items to."),
            ActionParam("item", "string", "Item id to give, e.g. 'bread'."),
            ActionParam("count", "int", "How many to give.", required=False),
        ],
    ))
