import pytest

from minebot.bot.movement import FOLLOW_STOP_DISTANCE, GOTO_STOP_DISTANCE, MovementController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.places import PlaceMemory


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket, so command translation can be tested without a
    running mod.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_goto(self, x, y, z, stop_distance=2.0):
        self.sent.append(("goto", {"x": x, "y": y, "z": z, "stop_distance": stop_distance}))

    async def send_follow(self, entity_id, stop_distance=2.0):
        self.sent.append(("follow", {"entity_id": entity_id, "stop_distance": stop_distance}))

    async def send_stop(self):
        self.sent.append(("stop", {}))

    async def send_chat(self, text):
        self.sent.append(("chat", {"text": text}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _movement(bridge, tracker=None, places=None, self_position=None, tmp_path=None) -> MovementController:
    return MovementController(
        bridge,
        tracker if tracker is not None else EntityTracker(),
        places if places is not None else PlaceMemory(tmp_path / "places.json"),
        self_position if self_position is not None else SelfPositionTracker(),
    )


@pytest.mark.asyncio
async def test_follow_with_explicit_name_sends_follow_for_that_entity(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)

    result = await movement.follow(None, "Alex")

    assert bridge.sent == [("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_follows_the_chat_sender(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)

    result = await movement.follow("Alex")

    assert bridge.sent == [("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_and_no_sender_raises(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    with pytest.raises(RuntimeError):
        await movement.follow(None)


@pytest.mark.asyncio
async def test_follow_unknown_player_sends_nothing_and_returns_a_message(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.follow(None, "NobodyHome")

    assert bridge.sent == []
    assert result.message is not None


@pytest.mark.asyncio
async def test_stop_sends_stop_command(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.stop(None)

    assert bridge.sent == [("stop", {})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_on_entity_added_resumes_follow_for_the_currently_followed_name(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)
    await movement.follow(None, "Alex")
    bridge.sent.clear()

    await movement.on_entity_added("Alex", 42)  # Alex reconnected with a new entity id

    assert bridge.sent == [("follow", {"entity_id": 42, "stop_distance": FOLLOW_STOP_DISTANCE})]


@pytest.mark.asyncio
async def test_on_entity_added_ignores_unrelated_players(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)
    await movement.follow(None, "Alex")
    bridge.sent.clear()

    await movement.on_entity_added("SomeoneElse", 99)

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_on_entity_added_does_nothing_when_not_following_anyone(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    await movement.on_entity_added("Alex", 7)

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_stop_clears_the_followed_name_so_a_later_reconnect_does_not_resume_it(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)
    await movement.follow(None, "Alex")
    await movement.stop(None)
    bridge.sent.clear()

    await movement.on_entity_added("Alex", 42)

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_remember_saves_the_current_position(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 10.0, "y": 64.0, "z": -5.0, "yaw": 0.0, "pitch": 0.0}))
    bridge = RecordingBridge()
    movement = _movement(bridge, places=places, self_position=self_position)

    result = await movement.remember(None, "home")

    place = places.get("home")
    assert place is not None
    assert (place.x, place.y, place.z) == (10.0, 64.0, -5.0)
    assert result.message is not None


@pytest.mark.asyncio
async def test_remember_without_a_known_position_reports_it_cannot(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.remember(None, "home")

    assert result.message is not None
    assert "don't know where I am" in result.message


@pytest.mark.asyncio
async def test_goto_explicit_coordinates(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.goto(None, 10, 64, -5)

    assert bridge.sent == [("goto", {"x": 10.0, "y": 64, "z": -5, "stop_distance": GOTO_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_goto_resolves_a_known_player(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex", x=1.0, y=2.0, z=3.0)
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)

    result = await movement.goto(None, "Alex")

    assert bridge.sent == [("goto", {"x": 1.0, "y": 2.0, "z": 3.0, "stop_distance": GOTO_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_goto_resolves_a_remembered_place(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    places.remember("home", 10.0, 64.0, -5.0)
    bridge = RecordingBridge()
    movement = _movement(bridge, places=places, tmp_path=tmp_path)

    result = await movement.goto(None, "home")

    assert bridge.sent == [("goto", {"x": 10.0, "y": 64.0, "z": -5.0, "stop_distance": GOTO_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_goto_unknown_target_sends_nothing(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.goto(None, "Nowhere")

    assert bridge.sent == []
    assert result.message is not None


@pytest.mark.asyncio
async def test_goto_clears_the_followed_name(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex", x=1.0, y=2.0, z=3.0)
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)
    await movement.follow(None, "Alex")

    await movement.goto(None, "Alex")
    bridge.sent.clear()
    await movement.on_entity_added("Alex", 99)

    assert bridge.sent == []
