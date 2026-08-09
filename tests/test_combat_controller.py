import pytest

from minebot.bot.combat import CombatController
from minebot.bot.player_intention import PlayerIntention, PlayerIntentionController
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

    async def send_defend(self, player_name=None):
        self.sent.append(("defend", {"player_name": player_name}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _combat(bridge, tracker=None) -> CombatController:
    return CombatController(bridge, tracker if tracker is not None else EntityTracker(), PlayerIntentionController())


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

    assert bridge.sent == [("defend", {"player_name": None})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_with_explicit_name_sends_defend_for_that_name():
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.defend(None, "Alex")

    assert bridge.sent == [("defend", {"player_name": "Alex"})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_unseen_player_still_sends_defend_by_name():
    # Unlike the old EntityTracker-gated behavior, a name the bot has
    # never seen as a loaded entity is still sent straight through --
    # PlayerController (mod-side) may still resolve it via the tab list
    # even though Python itself has no record of them (see combat.py's
    # own module docstring).
    bridge = RecordingBridge()
    combat = _combat(bridge)

    result = await combat.defend(None, "NobodyHome")

    assert bridge.sent == [("defend", {"player_name": "NobodyHome"})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_defend_sets_player_intention_to_defend():
    bridge = RecordingBridge()
    intention = PlayerIntentionController()
    combat = CombatController(bridge, EntityTracker(), intention)

    await combat.defend(None)

    assert intention.current == PlayerIntention.DEFEND


@pytest.mark.asyncio
async def test_kill_does_not_change_player_intention():
    # KILL is a one-shot self-loop mod-side, not a real PlayerIntention
    # value -- see PlayerIntentionState's own docstring -- so it must not
    # touch the tracked intention here either, even mid-DEFEND.
    bridge = RecordingBridge()
    intention = PlayerIntentionController()
    intention.set_intention(PlayerIntention.DEFEND)
    combat = CombatController(bridge, EntityTracker(), intention)

    await combat.kill(None, "zombie")

    assert intention.current == PlayerIntention.DEFEND
