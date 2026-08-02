from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker


def _entity_event(action: str, entity_id: int, name: str | None = None, x=None, y=None, z=None) -> ModEvent:
    data = {"action": action, "id": entity_id}
    if name is not None:
        data["name"] = name
    if x is not None:
        data["x"] = x
        data["y"] = y
        data["z"] = z
    return ModEvent(type="entity", data=data)


def test_add_then_find_by_id_and_name():
    tracker = EntityTracker()
    tracker.handle_event(_entity_event("add", 42, "Alex", 1.0, 2.0, 3.0))

    by_id = tracker.find_by_id(42)
    assert by_id is not None
    assert by_id.name == "Alex"
    assert (by_id.x, by_id.y, by_id.z) == (1.0, 2.0, 3.0)

    by_name = tracker.find_by_name("Alex")
    assert by_name is by_id


def test_move_event_updates_position_and_keeps_name():
    tracker = EntityTracker()
    tracker.handle_event(_entity_event("add", 42, "Alex", 1.0, 2.0, 3.0))
    tracker.handle_event(_entity_event("move", 42, x=5.0, y=2.0, z=3.0))

    entity = tracker.find_by_id(42)
    assert entity.x == 5.0
    assert entity.name == "Alex"  # move events carry no name, must be preserved
    assert tracker.find_by_name("Alex").id == 42


def test_remove_event_clears_both_lookups():
    tracker = EntityTracker()
    tracker.handle_event(_entity_event("add", 42, "Alex", 1.0, 2.0, 3.0))
    tracker.handle_event(_entity_event("remove", 42))

    assert tracker.find_by_id(42) is None
    assert tracker.find_by_name("Alex") is None


def test_find_by_id_and_name_return_none_when_unknown():
    tracker = EntityTracker()
    assert tracker.find_by_id(999) is None
    assert tracker.find_by_name("Nobody") is None


def test_non_entity_events_are_ignored():
    tracker = EntityTracker()
    tracker.handle_event(ModEvent(type="chat", data={"text": "hi"}))
    assert tracker.find_by_id(1) is None
