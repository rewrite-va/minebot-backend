import asyncio

import pytest

import minebot.bot.combat as combat_module
from minebot.bot.combat import CombatController


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket -- same shape as test_mining_controller's own
    RecordingBridge.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_attack(self, query, radius=64):
        self.sent.append(("attack", {"query": query, "radius": radius}))


@pytest.mark.asyncio
async def test_attack_with_no_query_asks_for_nearest_hostile():
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    task = asyncio.ensure_future(combat.attack(None))
    await asyncio.sleep(0)
    combat.on_attack_result({"success": True})
    result = await task

    assert bridge.sent == [("attack", {"query": None, "radius": 64})]
    assert result.message == "killed it"


@pytest.mark.asyncio
async def test_attack_with_a_query_names_the_target_in_the_reply():
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    task = asyncio.ensure_future(combat.attack(None, "zombie"))
    await asyncio.sleep(0)
    combat.on_attack_result({"success": True, "query": "zombie"})
    result = await task

    assert bridge.sent == [("attack", {"query": "zombie", "radius": 64})]
    assert result.message == "killed the zombie"


@pytest.mark.asyncio
async def test_attack_reports_a_failure_reason():
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    task = asyncio.ensure_future(combat.attack(None, "cow"))
    await asyncio.sleep(0)
    combat.on_attack_result({"success": False, "query": "cow", "reason": "couldn't reach the target"})
    result = await task

    assert "cow" in result.message
    assert "couldn't reach the target" in result.message


@pytest.mark.asyncio
async def test_attack_reports_a_bare_failure_reason_with_no_query():
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    task = asyncio.ensure_future(combat.attack(None))
    await asyncio.sleep(0)
    combat.on_attack_result({"success": False, "reason": "no hostiles found nearby"})
    result = await task

    assert result.message == "couldn't fight -- no hostiles found nearby"


@pytest.mark.asyncio
async def test_attack_times_out_if_the_mod_never_replies(monkeypatch):
    monkeypatch.setattr(combat_module, "ATTACK_TIMEOUT", 0.01)
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    result = await combat.attack(None, "zombie")  # on_attack_result never called -- times out fast (patched to 0.01s)

    assert "no response" in result.message


@pytest.mark.asyncio
async def test_attack_result_arriving_with_nothing_pending_is_ignored():
    bridge = RecordingBridge()
    combat = CombatController(bridge)

    combat.on_attack_result({"success": True})  # no attack() call in flight -- must not raise
