"""Auto-enters self-defend mode the instant the bot takes a hostile hit --
reacts to the mod's own `damage` event (see FINDINGS.md's wire-protocol
section), which already classifies WHETHER the hit came from a monster/
player attack (`hostile: true`) as opposed to fall damage, drowning, fire,
etc (`hostile: false`) -- that classification happens mod-side, from the
real DamageSource the client received, not re-derived here from a bare
health-value drop.

Reads PlayerIntentionController.current as the single authoritative
"is DEFEND already active" check -- not a separate flag of its own -- so
this always agrees with whatever !defend/!follow/!stop last actually did,
including transitions this trigger had no part in (a player's own !follow
typed after an earlier auto-defend correctly re-arms auto-defend for the
next hit, since CombatController.defend and MovementController.follow/
stop are the only things that ever call set_intention, and they always do
so as part of the very call that also sends the real wire command -- see
player_intention.py's own docstring for why that pairing can't drift).
"""

from __future__ import annotations

import asyncio
import logging

from minebot.bot.combat import CombatController
from minebot.bot.player_intention import PlayerIntention, PlayerIntentionController
from minebot.bridge.client import ModEvent

log = logging.getLogger("minebot.self_defense")


class SelfDefenseTrigger:
    def __init__(self, combat: CombatController, intention: PlayerIntentionController) -> None:
        self.combat = combat
        self.intention = intention

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "damage" or not event.data.get("hostile"):
            return

        if self.intention.current == PlayerIntention.DEFEND:
            log.debug("hostile hit taken but already defending -- not re-triggering")
            return

        attacker = event.data.get("attacker")
        log.info("hostile hit taken (attacker=%s) -- entering self-defend mode", attacker)
        asyncio.ensure_future(self.combat.defend(sender=None))
