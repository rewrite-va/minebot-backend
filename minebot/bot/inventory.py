"""Inventory actions -- !give, mirroring movement.py's shape: chat-
command-to-command translation only, all the real work (walking to the
recipient, the actual drop) happens inside the mod (see minebot-mod's
GiveTask). Restored from git history (see CLAUDE.md/git log for the
original inventory/equip/drop/give module this repo used to have, deleted
in 6a0b692's mass command strip and never brought back) -- scoped down to
just !give, matching prompt.txt's own grammar
(`!give <recipient> <item> <quantity>`, every argument optional) rather
than the old module's item-first/closest-player-fallback shape.

Items are named by bare item id (e.g. "diamond"), normalized to a full
"minecraft:<id>" registry id to match what InventoryTracker's snapshots
(and minebot-mod's InventoryReporter/dropItem) use -- already-namespaced
ids ("minecraft:diamond", or a future mod's own "othermod:thing") pass
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


def _normalize_item_id(item: str) -> str:
    return item if ":" in item else f"minecraft:{item}"


class InventoryController:
    def __init__(self, bridge: ModBridge, inventory: InventoryTracker, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.inventory = inventory
        self.tracker = tracker

    async def give(
        self, sender: str | None, recipient: str | None = None, item: str | None = None, quantity: float = 0,
    ) -> ActionResult:
        """!give [recipient] [item] [quantity] -- per prompt.txt's own
        design: with no recipient, gives to the caller (`sender`); with no
        item, gives back whatever the bot most recently picked up
        (InventoryTracker.last_gained_item -- resolved HERE, not by the
        mod's own Command.Give, so there's exactly one "what did we most
        recently pick up" implementation instead of two independently-
        tracked, possibly-disagreeing ones); with no quantity, gives the
        whole stack (`quantity <= 0`, matching minebot-mod's own
        InventoryController.dropItem semantics).
        """
        target_name = recipient if recipient else sender
        if target_name is None:
            raise RuntimeError(
                "no recipient given and no sender name available to give to "
                "(e.g. triggered from console/system chat, not a player message)"
            )

        entity = self.tracker.find_by_name(target_name)
        if entity is None:
            log.warning("give: no known entity named %s (not currently visible?)", target_name)
            return ActionResult(message=f"I can't see {target_name}")

        resolved_item = _normalize_item_id(item) if item else self.inventory.last_gained_item
        if resolved_item is None:
            log.info("give: no item specified and nothing has been picked up yet")
            return ActionResult(message="I haven't picked up anything to give")

        log.info("giving %s (qty=%s) to %s (entity %d)", resolved_item, quantity, target_name, entity.id)
        await self.bridge.send_give(entity.id, resolved_item, int(quantity))
        item_label = resolved_item.removeprefix("minecraft:")
        return ActionResult(message=f"ok, bringing {item_label} to {target_name}")


def register_inventory_actions(registry: ActionRegistry, inventory: InventoryController) -> None:
    registry.register(Action(
        name="give",
        description=(
            "Give an item to a player, walking it over to them first. If recipient is omitted, gives to "
            "whoever sent the command. If item is omitted, gives back whatever the bot most recently picked "
            "up. If quantity is omitted, gives the whole stack."
        ),
        handler=inventory.give,
        params=[
            ActionParam("recipient", "string", "Name of the player to give it to. Omit to give to whoever sent the command.", required=False),
            ActionParam("item", "string", "Item id to give, e.g. 'diamond'. Omit to give back the most recently picked-up item.", required=False),
            ActionParam("quantity", "int", "How many to give. Omit to give the whole stack.", required=False),
        ],
    ))
