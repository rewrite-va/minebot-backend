"""Combat actions -- kill/defend, driven by minebot-mod's goal-based
control channel, same shape as movement.py's follow/stop. !kill's target
is a MOB type ("zombie") or nothing at all ("nearest hostile") -- Python
has no non-player entity tracking to resolve that itself, so the raw
query is forwarded as-is and resolved client-side by the mod's own
GeneralKillNode (mirroring the deleted EntityFinder's old shape -- see
that class's own docstring in the mod repo). !defend's target, when
given, is a PLAYER name -- resolved to an entity id here via
EntityTracker, same as !follow's own name resolution, since Python does
track players (just not arbitrary mobs).
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
        log.info("starting combat -- target=%s", target or "(nearest hostile)")
        await self.bridge.send_kill(target)
        return ActionResult(message=f"ok, fighting {target}" if target else "ok, fighting the nearest hostile")

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
            ActionParam("target", "string", "Mob type to fight (e.g. \"zombie\"). Omit for the nearest hostile mob.", required=False),
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
