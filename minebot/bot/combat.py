"""Combat actions -- !attack/!kill, mirroring movement.py/mining.py's
shape: chat-command-to-command translation only, all the real work
(target resolution, walking into melee range, the actual attack) happens
inside the mod. See PENDING.md's "Combat" section for the gap analysis
this came out of.

Unlike !collect, !attack has no counted loop at all -- it's a single
mod-side run from "find a target" through "it's dead" (or abandoned),
matching mindcraft's own attackNearest/attackEntity shape and the
explicit ask in PENDING.md ("attack and kill the nearest entity of a
given type"), not "kill N of them" (that's what !collect <entity> already
covers, for whoever wants counted kills-for-drops instead of a fight).
"""

from __future__ import annotations

import asyncio
import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge

log = logging.getLogger("minebot.combat")

# Generous: covers walking to a distant target plus a real fight, bounded
# so a genuinely stuck/forgotten attempt doesn't hang forever. The mod's
# own ATTACK_TARGET_TIMEOUT_TICKS (10s) and low-health abort both resolve
# well within this in practice; this is just the outer safety net for "no
# response at all" (e.g. a dropped connection).
ATTACK_TIMEOUT = 120.0


class CombatController:
    def __init__(self, bridge: ModBridge) -> None:
        self.bridge = bridge
        # Same single-slot pending-future pattern as MiningController's
        # _pending_collect_result -- only one !attack is ever in flight
        # at a time (chat commands are dispatched one at a time).
        self._pending_attack_result: asyncio.Future[dict] | None = None

    async def attack(self, sender: str | None, query: str | None = None) -> ActionResult:
        """Fights `query` (an entity type, e.g. "cow" or "zombie") if
        given, or the nearest hostile mob if not -- per PENDING.md's
        explicit ask: "!attack will attack the nearest hostile mob" with
        no argument.
        """
        result = asyncio.get_event_loop().create_future()
        self._pending_attack_result = result
        log.info("attacking %s", query or "nearest hostile")
        await self.bridge.send_attack(query)
        try:
            data = await asyncio.wait_for(result, timeout=ATTACK_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("attack: no response from mod within %.0fs", ATTACK_TIMEOUT)
            return ActionResult(message="I couldn't fight anything (no response)")
        finally:
            self._pending_attack_result = None

        # data["query"] mirrors back whatever the mod actually attacked --
        # None for a bare "nearest hostile" attack, same as this handler's
        # own `query` param -- so a message can name the target either
        # way it was resolved.
        target = data.get("query") or query
        if data.get("success"):
            return ActionResult(message=f"killed the {target}" if target else "killed it")
        reason = data.get("reason", "failed")
        return ActionResult(message=f"couldn't kill the {target} -- {reason}" if target else f"couldn't fight -- {reason}")

    def on_attack_result(self, data: dict) -> None:
        """Called from the run loop for every attack_result event -- resolves attack()'s pending future, if any."""
        if self._pending_attack_result is not None and not self._pending_attack_result.done():
            self._pending_attack_result.set_result(data)


def register_combat_actions(registry: ActionRegistry, combat: CombatController) -> None:
    action = Action(
        name="attack",
        description="Attack and fight an entity until it dies -- the nearest hostile mob if no type is given, or the nearest of a specific type.",
        handler=combat.attack,
        params=[
            ActionParam("query", "string", "Entity type to attack, e.g. \"zombie\" or \"cow\". Defaults to the nearest hostile mob.", required=False),
        ],
    )
    registry.register(action)
    registry.register(Action(
        name="kill",
        description="Alias for !attack.",
        handler=combat.attack,
        params=action.params,
    ))
