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
import math

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker, TrackedEntity
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.self_position import SelfPositionTracker

log = logging.getLogger("minebot.inventory")

GIVE_STOP_DISTANCE = 2.0


def _normalize_item_id(item: str) -> str:
    return item if ":" in item else f"minecraft:{item}"


class InventoryController:
    def __init__(
        self, bridge: ModBridge, inventory: InventoryTracker, tracker: EntityTracker, self_position: SelfPositionTracker,
    ) -> None:
        self.bridge = bridge
        self.inventory = inventory
        self.tracker = tracker
        self.self_position = self_position

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

    async def give(self, sender: str | None, item: str | None = None, player_name: str | None = None) -> ActionResult:
        """!give <item> [player_name] gives a specific item, same as any
        normal give. !give with no item (bare, or just a player name --
        "!give riterite" tries riterite as a currently-visible player
        first, falling back to treating it as an item name if it isn't
        one, same resolution order !find uses for entity-vs-block)
        instead drops back whatever the bot most recently gained (see
        InventoryTracker.gained_items) -- added so a player can quickly
        reclaim something the bot auto-grabbed by accident without
        needing to know/type its exact item id.
        """
        if item is not None and player_name is None:
            maybe_player = self.tracker.find_by_name(item)
            if maybe_player is not None:
                return await self._give_gained_item(maybe_player)
            return await self._give_specific_item(_normalize_item_id(item), None)

        if item is not None:
            return await self._give_specific_item(_normalize_item_id(item), player_name)

        entity = self._resolve_recipient(None)
        if entity is None:
            log.info("give: no players nearby to give to")
            return ActionResult(message="I don't see anyone nearby to give this to")
        return await self._give_gained_item(entity)

    async def _give_gained_item(self, entity: TrackedEntity) -> ActionResult:
        gained = self.inventory.gained_items()
        if not gained:
            log.info("give: no recently gained item to give back")
            return ActionResult(message="I haven't picked up anything recently")

        if len(gained) > 1:
            # Each `inventory` broadcast is meant to correspond to one
            # atomic change (see InventoryTracker's docstring), so this
            # shouldn't normally happen -- but if it does, guessing which
            # one the player actually meant would risk repeating the
            # exact live bug this replaced ("biggest increase" picking
            # the wrong item). Ask instead of guessing.
            names = ", ".join(g.removeprefix("minecraft:") for g in gained)
            log.info("give: multiple items gained at once (%s), asking which one", names)
            return ActionResult(message=f"I recently got several things: {names} -- say !give <item> to pick one")

        item = gained[0]
        entry = self.inventory.find_by_item(item)
        if entry is None:
            # Gained, then presumably already used/dropped/crafted away
            # before !give was typed -- gained_items() only reflects the
            # last snapshot transition, not whether it's still on hand,
            # so this is a real, expected case to guard, not a bug.
            log.info("give: no longer carrying %s (already used/dropped?)", item)
            return ActionResult(message=f"I don't have any {item.removeprefix('minecraft:')} anymore")

        return await self._send_give(entity, entry.slot, entry.count, item)

    async def _give_specific_item(self, item: str, player_name: str | None) -> ActionResult:
        entry = self.inventory.find_by_item(item)
        if entry is None:
            log.info("give: not carrying any %s", item)
            return ActionResult(message=f"I don't have any {item.removeprefix('minecraft:')}")

        entity = self._resolve_recipient(player_name)
        if entity is None:
            if player_name:
                log.warning("give: no known entity named %s (not currently visible?)", player_name)
                return ActionResult(message=f"I can't see {player_name}")
            log.info("give: no players nearby to give to")
            return ActionResult(message="I don't see anyone nearby to give this to")

        return await self._send_give(entity, entry.slot, entry.count, item)

    def _resolve_recipient(self, player_name: str | None) -> TrackedEntity | None:
        if player_name:
            return self.tracker.find_by_name(player_name)
        return self._closest_player()

    async def _send_give(self, entity: TrackedEntity, slot: int, count: int, item: str) -> ActionResult:
        target_name = entity.name or "someone"
        log.info("giving %dx %s to %s (entity %d)", count, item, target_name, entity.id)
        await self.bridge.send_give(entity.id, slot, count, stop_distance=GIVE_STOP_DISTANCE)
        return ActionResult(message=f"ok, bringing {count}x {item.removeprefix('minecraft:')} to {target_name}")

    def _closest_player(self) -> TrackedEntity | None:
        position = self.self_position.current
        if position is None:
            return None
        closest: TrackedEntity | None = None
        closest_distance_sq = math.inf
        for entity in self.tracker.all():
            distance_sq = (entity.x - position.x) ** 2 + (entity.y - position.y) ** 2 + (entity.z - position.z) ** 2
            if distance_sq < closest_distance_sq:
                closest = entity
                closest_distance_sq = distance_sq
        return closest


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
        description=(
            "Give a specific item to a player. If item is omitted, gives back whatever the bot most recently "
            "picked up instead -- lets a player quickly reclaim something the bot auto-grabbed by accident. "
            "If player_name is omitted, gives to whoever's closest."
        ),
        handler=inventory.give,
        params=[
            ActionParam("item", "string", "Item id to give, e.g. 'bread'. Omit to give back the most recently picked-up item.", required=False),
            ActionParam("player_name", "string", "Name of the player to give it to.", required=False),
        ],
    ))
