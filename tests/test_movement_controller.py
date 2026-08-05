import asyncio

import pytest

import minebot.bot.movement as movement_module
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

    async def send_find(self, query, radius=64):
        self.sent.append(("find", {"query": query, "radius": radius}))

    async def send_find_chest(self, entity_id):
        self.sent.append(("find_chest", {"entity_id": entity_id}))


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
async def test_save_location_saves_the_callers_position_not_the_bots(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite", x=10.0, y=64.0, z=-5.0)
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 999.0, "y": 999.0, "z": 999.0, "yaw": 0.0, "pitch": 0.0}))
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker=tracker, places=places, self_position=self_position)

    result = await movement.save("riterite", "location", "home")

    place = places.get("home")
    assert place is not None
    assert (place.x, place.y, place.z) == (10.0, 64.0, -5.0)
    assert result.message is not None


@pytest.mark.asyncio
async def test_save_without_a_known_sender_reports_it_cannot(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.save(None, "location", "home")

    assert result.message is not None
    assert "don't know who" in result.message


@pytest.mark.asyncio
async def test_save_for_an_untracked_sender_reports_it_cannot(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    result = await movement.save("someoneNotVisible", "location", "home")

    assert result.message is not None
    assert "can't see you" in result.message


@pytest.mark.asyncio
async def test_save_with_an_unknown_kind_reports_it_cannot(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker=tracker, tmp_path=tmp_path)

    result = await movement.save("riterite", "bogus", "home")

    assert bridge.sent == []
    assert "bogus" in result.message


@pytest.mark.asyncio
async def test_save_chest_sends_find_chest_and_saves_the_result(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker=tracker, places=places, tmp_path=tmp_path)

    task = asyncio.ensure_future(movement.save("riterite", "chest", "stash"))
    await asyncio.sleep(0)  # let save() send find_chest and start awaiting the result
    movement.on_find_chest_result({"found": True, "x": 10, "y": 64, "z": -5})
    result = await task

    assert bridge.sent == [("find_chest", {"entity_id": 7})]
    place = places.get("stash")
    assert place is not None
    assert (place.x, place.y, place.z) == (10, 64, -5)
    assert result.message is not None


@pytest.mark.asyncio
async def test_save_chest_reports_when_the_caller_is_not_looking_at_a_chest(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker=tracker, tmp_path=tmp_path)

    task = asyncio.ensure_future(movement.save("riterite", "chest", "stash"))
    await asyncio.sleep(0)
    movement.on_find_chest_result({"found": False})
    result = await task

    assert "chest" in result.message


@pytest.mark.asyncio
async def test_save_chest_times_out_if_the_mod_never_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(movement_module, "FIND_RESULT_TIMEOUT", 0.01)
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite")
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker=tracker, tmp_path=tmp_path)

    result = await movement.save("riterite", "chest", "stash")  # on_find_chest_result never called

    assert "no response" in result.message


@pytest.mark.asyncio
async def test_on_find_chest_result_with_nothing_pending_is_ignored(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    movement.on_find_chest_result({"found": True, "x": 1, "y": 2, "z": 3})  # should not raise


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


@pytest.mark.asyncio
async def test_find_walks_to_a_found_result_and_confirms_arrival(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    find_task = asyncio.ensure_future(movement.find(None, "cow"))
    await asyncio.sleep(0)  # let find() send the command and start awaiting the result
    movement.on_find_result({"found": True, "kind": "entity", "x": 3.0, "y": 5.0, "z": 9.0})
    await asyncio.sleep(0)  # let find() send the "found at" chat message and start awaiting arrival
    movement.on_arrived()
    result = await find_task

    assert bridge.sent == [
        ("find", {"query": "cow", "radius": 64}),
        ("chat", {"text": "found cow at (3, 5, 9), going there"}),
        ("goto", {"x": 3.0, "y": 5.0, "z": 9.0, "stop_distance": GOTO_STOP_DISTANCE}),
    ]
    assert result.message == "here is the cow"


@pytest.mark.asyncio
async def test_find_reports_when_it_never_arrives(tmp_path, monkeypatch):
    # Regression test: a player reported the bot "seems stuck" after
    # !find with no way to tell whether it was still en route or had
    # actually given up -- find() now distinguishes "still walking" from
    # "gave up" instead of silently going quiet either way.
    monkeypatch.setattr(movement_module, "FIND_ARRIVAL_TIMEOUT", 0.01)
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    find_task = asyncio.ensure_future(movement.find(None, "cow"))
    await asyncio.sleep(0)
    movement.on_find_result({"found": True, "kind": "entity", "x": 3.0, "y": 5.0, "z": 9.0})
    result = await find_task  # on_arrived is never called -- should time out fast (patched to 0.01s)

    assert "stuck" in result.message




@pytest.mark.asyncio
async def test_find_reports_when_nothing_is_found(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    find_task = asyncio.ensure_future(movement.find(None, "diamond_ore"))
    await asyncio.sleep(0)
    movement.on_find_result({"found": False})
    result = await find_task

    assert bridge.sent == [("find", {"query": "diamond_ore", "radius": 64})]
    assert "couldn't find" in result.message


@pytest.mark.asyncio
async def test_find_reports_an_unrecognized_query_differently_from_not_found(tmp_path):
    # Regression test: BLOCK/ENTITY_TYPE are DefaultedRegistry mod-side,
    # so a genuinely unrecognized query (a typo, a made-up word) used to
    # report the exact same "couldn't find X nearby" as a real block/
    # entity type that's simply out of range -- a player asked for these
    # to be told apart ("!find aaa" vs "!find allay" with none around).
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    find_task = asyncio.ensure_future(movement.find(None, "aaa"))
    await asyncio.sleep(0)
    movement.on_find_result({"found": False, "recognized": False})
    result = await find_task

    assert "isn't a block or mob" in result.message
    assert "couldn't find" not in result.message


@pytest.mark.asyncio
async def test_find_clears_the_followed_name(tmp_path):
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex", x=1.0, y=2.0, z=3.0)
    bridge = RecordingBridge()
    movement = _movement(bridge, tracker, tmp_path=tmp_path)
    await movement.follow(None, "Alex")

    find_task = asyncio.ensure_future(movement.find(None, "cow"))
    await asyncio.sleep(0)
    movement.on_find_result({"found": False})
    await find_task
    bridge.sent.clear()

    await movement.on_entity_added("Alex", 99)

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_on_find_result_with_nothing_pending_is_ignored(tmp_path):
    bridge = RecordingBridge()
    movement = _movement(bridge, tmp_path=tmp_path)

    movement.on_find_result({"found": True, "kind": "block", "x": 1.0, "y": 2.0, "z": 3.0})  # should not raise
