"""Inventory command handlers -- !inventory/!equip/!drop/!give, mirroring
movement.py's shape: chat-command-to-command translation only, all the
real work (container clicks, drop actions, walking to a recipient) happens
inside the mod. Items are named by bare item id in chat (e.g. "bread"),
normalized to a full "minecraft:<id>" registry id to match what
InventoryTracker's snapshots (and minebot-mod's InventoryReporter) use --
already-namespaced ids ("minecraft:bread", or a future mod's own
"othermod:thing") pass through unchanged.
"""

from __future__ import annotations

import logging

from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.commands.registry import CommandRegistry

log = logging.getLogger("minebot.inventory")

GIVE_STOP_DISTANCE = 2.0


def _normalize_item_id(item: str) -> str:
    return item if ":" in item else f"minecraft:{item}"


class InventoryController:
    def __init__(self, bridge: ModBridge, inventory: InventoryTracker, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.inventory = inventory
        self.tracker = tracker

    async def inventory_report(self, sender: str | None) -> None:
        slots = self.inventory.slots()
        if not slots:
            await self.bridge.send_chat("my inventory is empty")
            return
        summary = ", ".join(f"{s.count}x {s.item.removeprefix('minecraft:')}" for s in slots)
        await self.bridge.send_chat(f"inventory: {summary}")

    async def equip(self, sender: str | None, item: str) -> None:
        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("equip: not carrying any %s", item)
            await self.bridge.send_chat(f"I don't have any {item}")
            return
        await self.bridge.send_equip(entry.slot)

    async def drop(self, sender: str | None, item: str, count: float = 1) -> None:
        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("drop: not carrying any %s", item)
            await self.bridge.send_chat(f"I don't have any {item}")
            return
        await self.bridge.send_drop(entry.slot, int(count))

    async def give(self, sender: str | None, player_name: str, item: str, count: float = 1) -> None:
        entity = self.tracker.find_by_name(player_name)
        if entity is None:
            log.warning("give: no known entity named %s (not currently visible?)", player_name)
            await self.bridge.send_chat(f"I can't see {player_name}")
            return

        entry = self.inventory.find_by_item(_normalize_item_id(item))
        if entry is None:
            log.warning("give: not carrying any %s", item)
            await self.bridge.send_chat(f"I don't have any {item}")
            return

        log.info("giving %dx %s to %s (entity %d)", int(count), item, player_name, entity.id)
        await self.bridge.send_give(entity.id, entry.slot, int(count), stop_distance=GIVE_STOP_DISTANCE)


def register_inventory_commands(registry: CommandRegistry, inventory: InventoryController) -> None:
    registry.register("inventory", inventory.inventory_report)
    registry.register("equip", inventory.equip)
    registry.register("drop", inventory.drop)
    registry.register("give", inventory.give)
