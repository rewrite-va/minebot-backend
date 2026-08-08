"""Movement actions -- follow/stop/pickup/sleep, driven by minebot-mod's goal-based
control channel instead of computing movement ourselves. All the real work
(yaw-toward-target, forward/jump timing, actual physics) happens inside
the mod, which runs a real Minecraft client; this class is just the
goal-translation layer, usable both as a chat command and an LLM tool call
via ActionRegistry.

Every handler takes `sender` as its first argument, matching
ActionRegistry's dispatch contract -- !follow uses `sender` as the default
target so "!follow" with no argument follows whoever typed it; an explicit
"!follow name" resolves the name through EntityTracker's name->id
mapping (fed by the mod's own entity events) instead.
"""

from __future__ import annotations

import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker

log = logging.getLogger("minebot.movement")

FOLLOW_STOP_DISTANCE = 2.0


class MovementController:
    def __init__(self, bridge: ModBridge, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.tracker = tracker

    async def follow(self, sender: str | None, player_name: str | None = None) -> ActionResult:
        target_name = player_name if player_name else sender
        if target_name is None:
            raise RuntimeError(
                "no player name given and no sender name available to follow "
                "(e.g. triggered from console/system chat, not a player message)"
            )

        entity = self.tracker.find_by_name(target_name)
        if entity is None:
            log.warning("follow: no known entity named %s (not currently visible?)", target_name)
            return ActionResult(message=f"I can't see {target_name}")

        log.info("starting follow of %s (entity %d)", target_name, entity.id)
        await self.bridge.send_follow(entity.id, stop_distance=FOLLOW_STOP_DISTANCE)
        return ActionResult(message=f"ok, following {target_name}")

    async def stop(self, sender: str | None) -> ActionResult:
        await self.bridge.send_stop()
        return ActionResult(message="ok, stopped")

    async def pickup(self, sender: str | None) -> ActionResult:
        # No name resolution needed -- unlike follow, this takes no
        # target argument at all (the mod resolves "near the bot" itself,
        # from the bot's own live position at the moment this arrives --
        # see minebot-mod's Command.Pickup/LegsPickupItemsNode).
        await self.bridge.send_pickup()
        return ActionResult(message="ok, picking up nearby items")

    async def sleep(self, sender: str | None) -> ActionResult:
        # Same no-target shape as pickup -- "nearest bed" is resolved
        # entirely mod-side (BlockFinder.findNearestBed), since Python has
        # no block-scanning of its own (see minebot-mod's
        # Command.Sleep/PlayerIntentionSleepNode).
        await self.bridge.send_sleep()
        return ActionResult(message="ok, looking for a bed")


def register_movement_actions(registry: ActionRegistry, movement: MovementController) -> None:
    registry.register(Action(
        name="follow",
        description="Walk to and follow a player. If no player_name is given, follows whoever sent the command.",
        handler=movement.follow,
        params=[
            ActionParam("player_name", "string", "Name of the player to follow.", required=False),
        ],
    ))
    registry.register(Action(
        name="stop",
        description="Stop whatever movement goal is currently active (follow) and stand still.",
        handler=movement.stop,
    ))
    registry.register(Action(
        name="pickup",
        description="Walk over and pick up every dropped item near the bot's current position.",
        handler=movement.pickup,
    ))
    registry.register(Action(
        name="sleep",
        description="Walk to the nearest bed and sleep in it.",
        handler=movement.sleep,
    ))
