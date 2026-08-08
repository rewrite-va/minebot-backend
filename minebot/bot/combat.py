"""Combat actions -- kill/defend, driven by minebot-mod's goal-based
control channel, same shape as movement.py's follow/stop. !kill's target
is first tried as a PLAYER name via EntityTracker (a one-shot trigger,
not a standing goal -- see minebot-mod's own Command.Kill docstring for
why this stays a one-time Python-side resolution, unlike follow/defend
below) and sent as a resolved entity_id; if no such player is currently
tracked, it's treated as a MOB type ("zombie") and forwarded as a raw
query string instead, or nothing at all for "nearest hostile" -- Python
has no non-player entity tracking to resolve a mob type itself, so that
case is resolved client-side by the mod's own PlayerIntentionKillNode
(mirroring the deleted EntityFinder's old shape -- see that class's own
docstring in the mod repo). !defend's target, when given, is a PLAYER
name sent straight through, NOT resolved to an entity id here -- the mod
itself (PlayerController) resolves whatever it actually needs from the
name fresh every tick, the same reasoning movement.py's own follow uses
(see its module docstring): a defend target who's never been visible
this session, or is currently out of range, can still be defended.
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

        # Sent straight through as a bare player name, not resolved to an
        # entity id here -- same reasoning as MovementController.follow's
        # own docstring: PlayerController (mod-side) resolves whatever it
        # actually needs from the name fresh every tick, via whichever
        # real channel currently has an answer, so a player who's never
        # been visible this session (or is currently out of range) can
        # still be defended, as long as they're actually on the server.
        log.info("starting defend mode -- protecting %s", player_name)
        await self.bridge.send_defend(player_name)
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
