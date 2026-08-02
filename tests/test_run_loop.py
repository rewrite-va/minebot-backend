import pytest

from minebot.bot.run_loop import run
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.commands.registry import CommandRegistry


class FakeBridge:
    """Replays a canned sequence of events instead of a real WebSocket
    connection, and records anything the run loop sends back via chat.
    """

    def __init__(self, events: list[ModEvent]):
        self._events = events
        self.sent_chat: list[str] = []

    async def events(self):
        for event in self._events:
            yield event

    async def send_chat(self, text: str) -> None:
        self.sent_chat.append(text)


@pytest.mark.asyncio
async def test_run_loop_feeds_entity_events_into_tracker():
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 1, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
    ])
    tracker = EntityTracker()
    registry = CommandRegistry()

    await run(bridge, registry, tracker)

    assert tracker.find_by_name("Alex") is not None


@pytest.mark.asyncio
async def test_run_loop_dispatches_chat_commands():
    calls = []

    async def ping_handler(sender):
        calls.append(sender)

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!ping"}),
    ])
    registry = CommandRegistry()
    registry.register("ping", ping_handler)

    await run(bridge, registry, EntityTracker())

    assert calls == ["Alex"]


@pytest.mark.asyncio
async def test_run_loop_replies_to_unknown_commands():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!doesnotexist"}),
    ])
    registry = CommandRegistry()

    await run(bridge, registry, EntityTracker())

    assert bridge.sent_chat == ["unknown command: !doesnotexist"]


@pytest.mark.asyncio
async def test_run_loop_does_not_reply_to_non_command_chat():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "hello there"}),
    ])
    registry = CommandRegistry()

    await run(bridge, registry, EntityTracker())

    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_run_loop_ignores_position_and_health_events_without_crashing():
    bridge = FakeBridge([
        ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0, "on_ground": True}),
        ModEvent(type="health", data={"health": 20.0}),
        ModEvent(type="health", data={"health": 0.0}),
    ])
    registry = CommandRegistry()

    await run(bridge, registry, EntityTracker())  # should not raise
