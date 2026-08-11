from minebot.bridge.client import ModEvent
from minebot.bridge.self_position import SelfPositionTracker


def test_starts_with_no_known_position():
    tracker = SelfPositionTracker()
    assert tracker.current is None


def test_updates_from_position_events():
    tracker = SelfPositionTracker()
    tracker.handle_event(ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 90.0, "pitch": -10.0}))

    position = tracker.current
    assert position is not None
    assert (position.x, position.y, position.z) == (1.0, 2.0, 3.0)
    assert position.yaw == 90.0
    assert position.pitch == -10.0


def test_later_events_overwrite_earlier_ones():
    tracker = SelfPositionTracker()
    tracker.handle_event(ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0, "pitch": 0.0}))
    tracker.handle_event(ModEvent(type="position", data={"x": 5.0, "y": 6.0, "z": 7.0, "yaw": 0.0, "pitch": 0.0}))

    position = tracker.current
    assert (position.x, position.y, position.z) == (5.0, 6.0, 7.0)


def test_ignores_non_position_events():
    tracker = SelfPositionTracker()
    tracker.handle_event(ModEvent(type="chat", data={"text": "hi"}))
    assert tracker.current is None


def test_listener_fires_on_every_position_event_not_just_the_latest():
    tracker = SelfPositionTracker()
    seen = []
    tracker.add_listener(lambda pos: seen.append((pos.x, pos.y, pos.z)))

    tracker.handle_event(ModEvent(type="position", data={"x": 1.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    tracker.handle_event(ModEvent(type="position", data={"x": 2.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    tracker.handle_event(ModEvent(type="chat", data={"text": "hi"}))
    tracker.handle_event(ModEvent(type="position", data={"x": 3.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))

    assert seen == [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)]


def test_remove_listener_stops_further_notifications():
    tracker = SelfPositionTracker()
    seen = []

    def listener(pos):
        seen.append((pos.x, pos.y, pos.z))

    tracker.add_listener(listener)
    tracker.handle_event(ModEvent(type="position", data={"x": 1.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    tracker.remove_listener(listener)
    tracker.handle_event(ModEvent(type="position", data={"x": 2.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))

    assert seen == [(1.0, 0.0, 0.0)]
