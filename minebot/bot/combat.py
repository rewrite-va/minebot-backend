"""Combat actions -- kill/defend, driven by minebot-mod's goal-based
control channel, same shape as movement.py's follow/stop. !kill's target
is first tried as a PLAYER name via EntityTracker (same lookup !defend
and !follow already use, since Python does track players) and sent as a
resolved entity_id; if no such player is currently tracked, it's treated
as a MOB type ("zombie") and forwarded as a raw query string instead, or
nothing at all for "nearest hostile" -- Python has no non-player entity
tracking to resolve a mob type itself, so that case is resolved
client-side by the mod's own PlayerIntentionKillNode (mirroring the
deleted EntityFinder's old shape -- see that class's own docstring in
the mod repo). !defend's target, when given, is always a PLAYER name --
resolved to an entity id here via EntityTracker the same way.
"""

from __future__ import annotations

import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker

log = logging.getLogger("minebot.combat")


class CombatController:
    def __init__(self, bridge: ModBridge, tracker: EntityTracker) -> None:
        self.bridge = bridge
        self.tracker = tracker

    async def kill(self, sender: str | None, target: str | None = None) -> ActionResult:
        if target is None:
            log.info("starting combat -- target=(nearest hostile)")
            await self.bridge.send_kill(None)
            return ActionResult(message="ok, fighting the nearest hostile")

        entity = self.tracker.find_by_name(target)
        if entity is not None:
            log.info("starting combat -- target=%s (player, entity %d)", target, entity.id)
            await self.bridge.send_kill(entity_id=entity.id)
            return ActionResult(message=f"ok, fighting {target}")

        log.info("starting combat -- target=%s (mob type)", target)
        await self.bridge.send_kill(query=target)
        return ActionResult(message=f"ok, fighting {target}")

    async def defend(self, sender: str | None, player_name: str | None = None) -> ActionResult:
        if player_name is None:
            log.info("starting defend mode -- protecting self")
            await self.bridge.send_defend(None)
            return ActionResult(message="ok, defending myself")

        entity = self.tracker.find_by_name(player_name)
        if entity is None:
            log.warning("defend: no known entity named %s (not currently visible?)", player_name)
            return ActionResult(message=f"I can't see {player_name}")

        log.info("starting defend mode -- protecting %s (entity %d)", player_name, entity.id)
        await self.bridge.send_defend(entity.id)
        return ActionResult(message=f"ok, defending {player_name}")


def register_combat_actions(registry: ActionRegistry, combat: CombatController) -> None:
    registry.register(Action(
        name="kill",
        description="Fight a target. If no target is given, fights the nearest hostile mob.",
        handler=combat.kill,
        params=[
            ActionParam("target", "string", "Player name or mob type to fight (e.g. \"Steve\" or \"zombie\"). Omit for the nearest hostile mob.", required=False),
        ],
    ))
    registry.register(Action(
        name="defend",
        description="Enter standing defense mode: auto-fights the nearest hostile threatening the target, staying close to it. Defends the bot itself if no target is given.",
        handler=combat.defend,
        params=[
            ActionParam("player_name", "string", "Name of the player to defend. Omit to defend the bot itself.", required=False),
        ],
    ))
