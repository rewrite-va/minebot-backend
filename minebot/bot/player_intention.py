"""Python-side mirror of minebot-mod's own PlayerIntentionState axis
(IDLE/FOLLOW/DEFEND -- see minebot-mod's PlayerIntentionState.java for the
full docstring on this axis). KILL is deliberately not a value here,
matching mod-side: PlayerIntentionKillNode is a one-shot self-loop that
returns straight back to whatever PlayerIntention already said once it
finishes, so !kill never actually transitions this axis, mod-side or here.

This is the single authoritative source, Python-side, for "what standing
goal is currently active" -- CombatController.defend/MovementController.
follow/stop each call set_intention() themselves as part of sending their
own wire command (never a separate step some caller has to remember),
so reading .current is always in sync with whatever was last actually
sent to the mod. Built specifically so SelfDefenseTrigger can ask "is
DEFEND already active?" without keeping its own separate, driftable copy
of that fact -- see its own docstring for why an earlier version that did
exactly that (a bare bool on CombatController, set on defend() and never
correctly cleared by follow()/stop()) was replaced with this.
"""

from __future__ import annotations

import enum
import logging

log = logging.getLogger("minebot.player_intention")


class PlayerIntention(enum.Enum):
    IDLE = "IDLE"
    FOLLOW = "FOLLOW"
    DEFEND = "DEFEND"


class PlayerIntentionController:
    def __init__(self) -> None:
        self.current = PlayerIntention.IDLE

    def set_intention(self, intention: PlayerIntention) -> None:
        if intention == self.current:
            return
        log.info("player intention: %s -> %s", self.current.value, intention.value)
        self.current = intention
