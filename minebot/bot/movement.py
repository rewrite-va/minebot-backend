"""Movement actions -- follow/stop, driven by minebot-mod's goal-based
control channel instead of computing movement ourselves. All the real work
(yaw-toward-target, forward/jump timing, actual physics) happens inside
the mod, which runs a real Minecraft client; this class is just the
goal-translation layer, usable both as a chat command and an LLM tool call
via ActionRegistry.

Every handler takes `sender` as its first argument, matching
ActionRegistry's dispatch contract -- !follow uses `sender` as the default
target so "!follow" with no argument follows whoever typed it; an explicit
"!follow(\"name\")" resolves the name through EntityTracker's name->id
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
        # The mod's FOLLOW goal is pinned to a fixed entity ID, but a
        # player who disconnects and reconnects gets a brand new one --
        # the old id then never resolves again and the bot just stands
        # idle forever with no error (found live: !follow silently
        # stopped working the moment the followed player rejoined).
        # Tracking the *name* we're supposed to be following (as opposed
        # to just fire-and-forgetting the id to the mod) lets
        # on_entity_added re-issue `follow` with the fresh id the moment
        # that name reappears.
        self._following_name: str | None = None

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

        self._following_name = target_name
        log.info("starting follow of %s (entity %d)", target_name, entity.id)
        await self.bridge.send_follow(entity.id, stop_distance=FOLLOW_STOP_DISTANCE)
        return ActionResult(message=f"ok, following {target_name}")

    async def stop(self, sender: str | None) -> ActionResult:
        self._following_name = None
        await self.bridge.send_stop()
        return ActionResult(message="ok, stopped")

    async def on_entity_added(self, name: str | None, entity_id: int) -> None:
        """Called from the run loop for every `entity` "add" event -- if
        the entity that just appeared is the player we're supposed to be
        following, re-sends `follow` with their fresh id so a
        disconnect/reconnect doesn't silently strand the goal on an id
        that will never resolve again.
        """
        if name is None or name != self._following_name:
            return
        log.info("resuming follow of %s after reconnect (new entity %d)", name, entity_id)
        await self.bridge.send_follow(entity_id, stop_distance=FOLLOW_STOP_DISTANCE)


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
        description="Stop whatever movement goal is currently active (follow/goto) and stand still.",
        handler=movement.stop,
    ))
