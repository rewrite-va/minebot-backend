import pytest

from minebot.bot.movement import FOLLOW_STOP_DISTANCE, MovementController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket, so command translation can be tested without a
    running mod.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_follow(self, entity_id, stop_distance=2.0):
        self.sent.append(("follow", {"entity_id": entity_id, "stop_distance": stop_distance}))

    async def send_stop(self):
        self.sent.append(("stop", {}))

    async def send_chat(self, text):
        self.sent.append(("chat", {"text": text}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _movement(bridge, tracker=None) -> MovementController:
    return MovementController(bridge, tracker if tracker is not None else EntityTracker())


@pytest.mark.asyncio
async def test_follow_with_explicit_name_sends_follow_for_that_entity():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker)

    result = await movement.follow(None, "Alex")

    assert bridge.sent == [("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_follows_the_chat_sender():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker)

    result = await movement.follow("Alex")

    assert bridge.sent == [("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_and_no_sender_raises():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    with pytest.raises(RuntimeError):
        await movement.follow(None)


@pytest.mark.asyncio
async def test_follow_unknown_player_sends_nothing_and_returns_a_message():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.follow(None, "NobodyHome")

    assert bridge.sent == []
    assert result.message is not None


@pytest.mark.asyncio
async def test_stop_sends_stop_command():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.stop(None)

    assert bridge.sent == [("stop", {})]
    assert result.message is not None
