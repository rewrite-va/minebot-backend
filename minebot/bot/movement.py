"""Movement actions -- follow/stop/pickup/sleep, driven by minebot-mod's goal-based
control channel instead of computing movement ourselves. All the real work
(yaw-toward-target, forward/jump timing, actual physics) happens inside
the mod, which runs a real Minecraft client; this class is just the
goal-translation layer, usable both as a chat command and an LLM tool call
via ActionRegistry.

Every handler takes `sender` as its first argument, matching
ActionRegistry's dispatch contract -- !follow uses `sender` as the default
target so "!follow" with no argument follows whoever typed it; an explicit
"!follow name" is sent straight through as a bare player name, not
resolved to an entity id here. The mod itself (PlayerController, see its
own docstring) resolves whatever it actually needs from that name, fresh
every tick, via whichever real channel currently has an answer (a loaded
entity, or the server-wide tab list) -- Python's own EntityTracker (fed
only by the mod's own entity-load events) can't tell whether a name is a
real online player at all, only whether the mod has happened to see them
loaded, which is exactly the gap this stopped gating on: a player who has
never been visible this session (or is currently out of simulation range)
can still be followed, as long as they're actually on the server.
"""

from __future__ import annotations

import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bot.player_intention import PlayerIntention, PlayerIntentionController
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker

log = logging.getLogger("minebot.movement")

FOLLOW_STOP_DISTANCE = 2.0


class MovementController:
    def __init__(self, bridge: ModBridge, tracker: EntityTracker, intention: PlayerIntentionController) -> None:
        self.bridge = bridge
        self.tracker = tracker
        self.intention = intention

    async def follow(self, sender: str | None, player_name: str | None = None) -> ActionResult:
        target_name = player_name if player_name else sender
        if target_name is None:
            raise RuntimeError(
                "no player name given and no sender name available to follow "
                "(e.g. triggered from console/system chat, not a player message)"
            )

        self.intention.set_intention(PlayerIntention.FOLLOW)
        log.info("starting follow of %s", target_name)
        await self.bridge.send_follow(target_name, stop_distance=FOLLOW_STOP_DISTANCE)
        return ActionResult(message=f"ok, following {target_name}")

    async def stop(self, sender: str | None) -> ActionResult:
        self.intention.set_intention(PlayerIntention.IDLE)
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
        # no block-scanning of its own. Mod-side, this is a queued
        # TaskController Task (SleepTask), the same home !give's GiveTask
        # already established, not a peer-SM concern (see minebot-mod's
        # Command.Sleep/task.SleepTask).
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
