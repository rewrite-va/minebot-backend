import asyncio

import pytest

from minebot.bot.combat import CombatController
from minebot.bot.player_intention import PlayerIntention, PlayerIntentionController
from minebot.bot.self_defense import SelfDefenseTrigger
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker


class RecordingBridge:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_defend(self, player_name=None):
        self.sent.append(("defend", {"player_name": player_name}))


def _trigger(bridge) -> tuple[SelfDefenseTrigger, CombatController]:
    intention = PlayerIntentionController()
    combat = CombatController(bridge, EntityTracker(), intention)
    return SelfDefenseTrigger(combat, intention), combat


async def _settle() -> None:
    # handle_event schedules combat.defend() via asyncio.ensure_future
    # rather than awaiting it directly (see self_defense.py's own
    # docstring) -- let the event loop actually run it before asserting.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_hostile_damage_triggers_self_defend():
    bridge = RecordingBridge()
    trigger, _ = _trigger(bridge)

    trigger.handle_event(ModEvent(type="damage", data={"hostile": True, "cause": "mob_attack", "attacker": "Zombie"}))
    await _settle()

    assert bridge.sent == [("defend", {"player_name": None})]


@pytest.mark.asyncio
async def test_non_hostile_damage_does_not_trigger_self_defend():
    bridge = RecordingBridge()
    trigger, _ = _trigger(bridge)

    trigger.handle_event(ModEvent(type="damage", data={"hostile": False, "cause": "fall", "attacker": None}))
    await _settle()

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_already_defending_is_not_re_triggered():
    bridge = RecordingBridge()
    trigger, combat = _trigger(bridge)
    await combat.defend(None, "Alex")  # standing order to defend someone else

    trigger.handle_event(ModEvent(type="damage", data={"hostile": True, "cause": "mob_attack", "attacker": "Zombie"}))
    await _settle()

    assert bridge.sent == [("defend", {"player_name": "Alex"})]


@pytest.mark.asyncio
async def test_non_damage_events_are_ignored():
    bridge = RecordingBridge()
    trigger, _ = _trigger(bridge)

    trigger.handle_event(ModEvent(type="health", data={"health": 5.0}))
    await _settle()

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_re_triggers_once_intention_leaves_defend():
    # intention.current is the single authoritative check -- once
    # something else (e.g. MovementController.follow/stop) moves it off
    # DEFEND, a fresh hostile hit must be able to trigger auto-defend
    # again, unlike the earlier bare-bool design this replaced.
    bridge = RecordingBridge()
    trigger, combat = _trigger(bridge)

    trigger.handle_event(ModEvent(type="damage", data={"hostile": True, "cause": "mob_attack", "attacker": "Zombie"}))
    await _settle()
    assert bridge.sent == [("defend", {"player_name": None})]

    combat.intention.set_intention(PlayerIntention.IDLE)  # e.g. a player's own !stop
    trigger.handle_event(ModEvent(type="damage", data={"hostile": True, "cause": "mob_attack", "attacker": "Skeleton"}))
    await _settle()

    assert bridge.sent == [("defend", {"player_name": None}), ("defend", {"player_name": None})]
