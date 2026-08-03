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
from minebot.bridge.self_position import SelfPositionTracker
from minebot.places import PlaceMemory

log = logging.getLogger("minebot.movement")

FOLLOW_STOP_DISTANCE = 2.0
GOTO_STOP_DISTANCE = 2.0


class MovementController:
    def __init__(
        self, bridge: ModBridge, tracker: EntityTracker, places: PlaceMemory, self_position: SelfPositionTracker,
    ) -> None:
        self.bridge = bridge
        self.tracker = tracker
        self.places = places
        self.self_position = self_position
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

    async def remember(self, sender: str | None, name: str) -> ActionResult:
        position = self.self_position.current
        if position is None:
            log.warning("remember: no known self position yet")
            return ActionResult(message="I don't know where I am yet, try again in a moment")

        self.places.remember(name, position.x, position.y, position.z)
        log.info("remembered %r at (%.1f, %.1f, %.1f)", name, position.x, position.y, position.z)
        return ActionResult(message=f"ok, remembered this place as {name}")

    async def goto(self, sender: str | None, target: str | float, y: float | None = None, z: float | None = None) -> ActionResult:
        """Resolves `target` in order: explicit coordinates (all three
        args given as numbers) -> a known player's current position ->
        a remembered place -> gives up. Block-type and entity-type
        resolution (PENDING.md's "universal !goto") aren't implemented
        yet -- neither block-scanning nor entity-type tracking beyond
        players exists on the mod side yet; this handler is written so
        adding those later is just two more `elif` branches here, no
        interface change needed.
        """
        self._following_name = None  # goto is a one-shot goal, not FOLLOW -- don't let a later reconnect resume-follow hijack it

        if y is not None and z is not None:
            x_coord = float(target)
            log.info("going to explicit coordinates (%.1f, %.1f, %.1f)", x_coord, y, z)
            await self.bridge.send_goto(x_coord, y, z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to ({x_coord:.0f}, {y:.0f}, {z:.0f})")

        target_name = str(target)

        entity = self.tracker.find_by_name(target_name)
        if entity is not None:
            log.info("going to player %s (entity %d)", target_name, entity.id)
            await self.bridge.send_goto(entity.x, entity.y, entity.z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to {target_name}")

        place = self.places.get(target_name)
        if place is not None:
            log.info("going to remembered place %r at (%.1f, %.1f, %.1f)", target_name, place.x, place.y, place.z)
            await self.bridge.send_goto(place.x, place.y, place.z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to {target_name}")

        log.warning("goto: %r is not a known player, remembered place, or coordinate", target_name)
        return ActionResult(message=f"I don't know where {target_name} is -- not a player I can see, not a saved place, and not coordinates")

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
    registry.register(Action(
        name="remember",
        description="Save the bot's current position under a name, for later !goto.",
        handler=movement.remember,
        params=[
            ActionParam("name", "string", "Name to remember this place as."),
        ],
    ))
    registry.register(Action(
        name="goto",
        description=(
            "Walk to a target: a player's name, a previously remembered place, or explicit x y z coordinates. "
            "Does not continue following -- use !follow for that."
        ),
        handler=movement.goto,
        params=[
            ActionParam("target", "string", "Player name, remembered place name, or the x coordinate if giving explicit x y z."),
            ActionParam("y", "float", "Y coordinate, only if giving explicit coordinates.", required=False),
            ActionParam("z", "float", "Z coordinate, only if giving explicit coordinates.", required=False),
        ],
    ))
