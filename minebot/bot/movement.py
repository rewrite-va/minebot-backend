"""Movement command handlers -- !follow/!stop, driven by minebot-mod's
goal-based control channel instead of computing movement ourselves. All
the real work (yaw-toward-target, forward/jump timing, actual physics)
happens inside the mod, which runs a real Minecraft client; this class is
just the chat-command-to-goal translation layer.

Every handler takes `sender` as its first argument, matching
CommandRegistry.dispatch's (message, *context_args) contract -- the run
loop calls `dispatch(text, sender)`. !follow uses `sender` as the default
target so "!follow" with no argument follows whoever typed it; an explicit
"!follow(\"name\")" resolves the name through EntityTracker's name->id
mapping (fed by the mod's own entity events) instead.
"""

from __future__ import annotations

import logging

from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.commands.registry import CommandRegistry

log = logging.getLogger("minebot.movement")

FOLLOW_STOP_DISTANCE = 2.0


class MovementController:
    def __init__(self, bridge: ModBridge, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.tracker = tracker

    async def follow(self, sender: str | None, player_name: str | None = None) -> None:
        target_name = player_name if player_name else sender
        if target_name is None:
            raise RuntimeError(
                "no player name given and no sender name available to follow "
                "(e.g. triggered from console/system chat, not a player message)"
            )

        entity = self.tracker.find_by_name(target_name)
        if entity is None:
            log.warning("follow: no known entity named %s (not currently visible?)", target_name)
            return

        log.info("starting follow of %s (entity %d)", target_name, entity.id)
        await self.bridge.send_follow(entity.id, stop_distance=FOLLOW_STOP_DISTANCE)

    async def stop(self, sender: str | None) -> None:
        await self.bridge.send_stop()


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("follow", movement.follow)
    registry.register("stop", movement.stop)
