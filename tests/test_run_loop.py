import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionResult
from minebot.bot.run_loop import run
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController

BOT_NAME = "minebot"
CONFIG = BotConfig(mod_host="0.0.0.0", mod_port=0, bot_name=BOT_NAME, trigger_words=(), mod_repo_path=None)


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


def _llm(bridge: FakeBridge, actions: ActionRegistry) -> LLMController:
    return LLMController(bridge, actions)  # default NullLLMProvider -- never replies


@pytest.mark.asyncio
async def test_run_loop_feeds_entity_events_into_tracker():
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 1, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
    ])
    tracker = EntityTracker()
    actions = ActionRegistry()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG)

    assert tracker.find_by_name("Alex") is not None


@pytest.mark.asyncio
async def test_run_loop_feeds_inventory_events_into_tracker():
    bridge = FakeBridge([
        ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": 0, "item": "minecraft:bread", "count": 5}]}),
    ])
    inventory = InventoryTracker()
    actions = ActionRegistry()

    await run(bridge, actions, EntityTracker(), inventory, _llm(bridge, actions), CONFIG)

    assert inventory.count_of("minecraft:bread") == 5


@pytest.mark.asyncio
async def test_run_loop_dispatches_chat_commands():
    calls = []

    async def ping_handler(sender):
        calls.append(sender)
        return ActionResult()

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!ping"}),
    ])
    actions = ActionRegistry()
    actions.register(Action(name="ping", description="", handler=ping_handler))

    await run(bridge, actions, EntityTracker(), InventoryTracker(), _llm(bridge, actions), CONFIG)

    assert calls == ["Alex"]


@pytest.mark.asyncio
async def test_run_loop_sends_action_result_message_to_chat():
    async def give_handler(sender, item):
        return ActionResult(message=f"here's your {item}")

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": '!give("bread")'}),
    ])
    actions = ActionRegistry()
    actions.register(Action(name="give", description="", handler=give_handler))

    await run(bridge, actions, EntityTracker(), InventoryTracker(), _llm(bridge, actions), CONFIG)

    assert bridge.sent_chat == ["here's your bread"]


@pytest.mark.asyncio
async def test_run_loop_replies_to_unknown_commands():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!doesnotexist"}),
    ])
    actions = ActionRegistry()

    await run(bridge, actions, EntityTracker(), InventoryTracker(), _llm(bridge, actions), CONFIG)

    assert bridge.sent_chat == ["unknown command: !doesnotexist"]


@pytest.mark.asyncio
async def test_run_loop_does_not_reply_to_unaddressed_non_command_chat():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "hello there"}),
    ])
    actions = ActionRegistry()

    await run(bridge, actions, EntityTracker(), InventoryTracker(), _llm(bridge, actions), CONFIG)

    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_run_loop_routes_bot_addressed_chat_to_the_llm_controller():
    handled = []

    class RecordingLLM:
        async def handle_chat(self, sender, text):
            handled.append((sender, text))

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": f"hey {BOT_NAME}, got food?"}),
    ])
    actions = ActionRegistry()

    await run(bridge, actions, EntityTracker(), InventoryTracker(), RecordingLLM(), CONFIG)

    assert handled == [("Alex", f"hey {BOT_NAME}, got food?")]


@pytest.mark.asyncio
async def test_run_loop_routes_trigger_word_chat_to_the_llm_controller():
    handled = []

    class RecordingLLM:
        async def handle_chat(self, sender, text):
            handled.append((sender, text))

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "hey buddy, got food?"}),
    ])
    actions = ActionRegistry()
    config = BotConfig(mod_host="0.0.0.0", mod_port=0, bot_name=BOT_NAME, trigger_words=("buddy",), mod_repo_path=None)

    await run(bridge, actions, EntityTracker(), InventoryTracker(), RecordingLLM(), config)

    assert handled == [("Alex", "hey buddy, got food?")]


@pytest.mark.asyncio
async def test_run_loop_ignores_position_health_death_and_respawn_events_without_crashing():
    bridge = FakeBridge([
        ModEvent(type="hello", data={"commit": "abc123", "built_at": "2026-01-01T00:00:00Z"}),
        ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0, "on_ground": True}),
        ModEvent(type="health", data={"health": 20.0}),
        ModEvent(type="health", data={"health": 0.0}),
        ModEvent(type="death", data={}),
        ModEvent(type="respawn", data={}),
    ])
    actions = ActionRegistry()

    await run(bridge, actions, EntityTracker(), InventoryTracker(), _llm(bridge, actions), CONFIG)  # should not raise
    assert bridge.sent_chat == []
