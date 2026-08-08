import pytest

from minebot.bot.combat import CombatController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket, so command translation can be tested without a
    running mod.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_kill(self, query=None, entity_id=None):
        self.sent.append(("kill", {"query": query, "entity_id": entity_id}))

    async def send_defend(self, entity_id=None):
        self.sent.append(("defend", {"entity_id": entity_id}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _combat(bridge, tracker=None) -> CombatController:
    return CombatController(bridge, tracker if tracker is not None else EntityTracker())


@pytest.mark.asyncio
async def test_kill_with_no_target_fights_nearest_hostile():
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.kill(None)

    assert bridge.sent == [("kill", {"query": None, "entity_id": None})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_kill_with_known_player_name_sends_entity_id():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    combat = _combat(bridge, tracker)

    result = await combat.kill(None, "Alex")

    assert bridge.sent == [("kill", {"query": None, "entity_id": 7})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_kill_with_unknown_name_falls_back_to_mob_type_query():
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.kill(None, "zombie")

    assert bridge.sent == [("kill", {"query": "zombie", "entity_id": None})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_with_no_target_defends_self():
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.defend(None)

    assert bridge.sent == [("defend", {"entity_id": None})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_with_known_player_name_sends_entity_id():
    tracker = EntityTracker()
    _add_player(tracker, 9, "Alex")
    bridge = RecordingBridge()
    combat = _combat(bridge, tracker)

    result = await combat.defend(None, "Alex")

    assert bridge.sent == [("defend", {"entity_id": 9})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_unknown_player_sends_nothing_and_returns_a_message():
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.defend(None, "NobodyHome")

    assert bridge.sent == []
    assert result.message is not None
