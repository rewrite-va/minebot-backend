import asyncio

import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bot.movement import FOLLOW_STOP_DISTANCE, MovementController, register_movement_actions
from minebot.bot.run_loop import run
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController

BOT_NAME = "minebot"
CONFIG = BotConfig(
    mod_host="0.0.0.0", mod_port=0, bot_name=BOT_NAME, trigger_words=(), mod_repo_path=None,
    observer_host="0.0.0.0", observer_port=0,
)


class FakeBridge:
    """Replays a canned sequence of events instead of a real WebSocket
    connection, and records anything the run loop sends back via chat.
    """

    def __init__(self, events: list[ModEvent]):
        self._events = events
        self.sent_chat: list[str] = []
        self.sent: list[tuple[str, dict]] = []

    async def events(self):
        for event in self._events:
            yield event

    async def send_chat(self, text: str) -> None:
        self.sent_chat.append(text)

    async def send_follow(self, entity_id, stop_distance=2.0):
        self.sent.append(("follow", {"entity_id": entity_id, "stop_distance": stop_distance}))

    async def send_stop(self):
        self.sent.append(("stop", {}))


def _llm(bridge: FakeBridge, actions: ActionRegistry) -> LLMController:
    return LLMController(bridge, actions)  # default NullLLMProvider -- never replies


def _movement(bridge: FakeBridge, tracker: EntityTracker) -> MovementController:
    return MovementController(bridge, tracker)


@pytest.mark.asyncio
async def test_run_loop_feeds_entity_events_into_tracker():
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 1, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
    ])
    tracker = EntityTracker()
    actions = ActionRegistry()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert tracker.find_by_name("Alex") is not None


@pytest.mark.asyncio
async def test_run_loop_feeds_inventory_events_into_tracker():
    bridge = FakeBridge([
        ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": 0, "item": "minecraft:bread", "count": 5}]}),
    ])
    inventory = InventoryTracker()
    tracker = EntityTracker()
    actions = ActionRegistry()

    await run(bridge, actions, tracker, inventory, _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert inventory.count_of("minecraft:bread") == 5


@pytest.mark.asyncio
async def test_run_loop_feeds_position_events_into_self_position_tracker():
    bridge = FakeBridge([
        ModEvent(type="position", data={"x": 10.0, "y": 64.0, "z": -5.0, "yaw": 0.0, "pitch": 0.0}),
    ])
    tracker = EntityTracker()
    actions = ActionRegistry()
    self_position = SelfPositionTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, self_position)

    assert self_position.current is not None
    assert (self_position.current.x, self_position.current.y, self_position.current.z) == (10.0, 64.0, -5.0)


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
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert calls == ["Alex"]


@pytest.mark.asyncio
async def test_run_loop_sends_action_result_message_to_chat():
    async def give_handler(sender, item):
        return ActionResult(message=f"here's your {item}")

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!give bread"}),
    ])
    actions = ActionRegistry()
    actions.register(Action(
        name="give", description="", handler=give_handler,
        params=[ActionParam("item", "string", "")],
    ))
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert bridge.sent_chat == ["here's your bread"]


@pytest.mark.asyncio
async def test_run_loop_replies_to_unknown_commands():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!doesnotexist"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert bridge.sent_chat == ["unknown command: !doesnotexist"]


@pytest.mark.asyncio
async def test_run_loop_does_not_reply_to_unaddressed_non_command_chat():
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "hello there"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

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
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), RecordingLLM(), CONFIG, SelfPositionTracker())

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
    config = BotConfig(
        mod_host="0.0.0.0", mod_port=0, bot_name=BOT_NAME, trigger_words=("buddy",), mod_repo_path=None,
        observer_host="0.0.0.0", observer_port=0,
    )
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), RecordingLLM(), config, SelfPositionTracker())

    assert handled == [("Alex", "hey buddy, got food?")]


@pytest.mark.asyncio
async def test_run_loop_ignores_position_and_health_events_without_crashing():
    bridge = FakeBridge([
        ModEvent(type="hello", data={"commit": "abc123", "built_at": "2026-01-01T00:00:00Z"}),
        ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0, "on_ground": True}),
        ModEvent(type="health", data={"health": 20.0}),
        ModEvent(type="health", data={"health": 0.0}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())  # should not raise
    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_run_loop_announces_death_in_chat():
    bridge = FakeBridge([
        ModEvent(type="death", data={}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert bridge.sent_chat == ["I died"]


@pytest.mark.asyncio
async def test_run_loop_does_not_announce_respawn_in_chat():
    bridge = FakeBridge([
        ModEvent(type="death", data={}),
        ModEvent(type="respawn", data={}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert bridge.sent_chat == ["I died"]


class StuckThenFollowBridge(FakeBridge):
    """Models a chat command whose handler never resolves (e.g. it's
    awaiting a wire reply the mod never sends) followed shortly after by
    an unrelated !follow chat message, mirroring how a player would
    actually recover from a stuck command -- the exact live scenario this
    guards: a stuck command leaving the bot unresponsive to everything
    typed after it.
    """

    def __init__(self, stuck_event: ModEvent):
        super().__init__([
            ModEvent(type="entity", data={"action": "add", "id": 7, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
            stuck_event,
        ])
        self._follow_sent = asyncio.Event()

    async def send_follow(self, entity_id, stop_distance=2.0):
        await super().send_follow(entity_id, stop_distance)
        self._follow_sent.set()

    async def events(self):
        async for event in super().events():
            yield event
        # The stuck command's own handler never gets a reply -- this is
        # where a player would type something else after noticing the
        # bot isn't responding.
        yield ModEvent(type="chat", data={"sender": "Alex", "text": "!follow"})
        await self._follow_sent.wait()


@pytest.mark.asyncio
async def test_run_loop_lets_a_new_chat_command_interrupt_a_stuck_one():
    # Regression test (originally reported against !collect, which no
    # longer exists on either side -- see PENDING.md's action-registry
    # strip-down): a chat command whose handler suspends forever (no
    # reply ever arrives from the mod) used to leave every later command
    # (!follow, !stop, ...) silently doing nothing, since the old run loop
    # awaited each chat command fully before looking at the next one, so
    # they just sat queued behind the stuck call for its full timeout.
    # Chat commands now run as independent, cancellable tasks (see
    # run_loop.py's own docstring) -- a new command cancels whatever
    # command is still running instead of waiting for it, so !follow here
    # must get dispatched and replied to promptly even though the stuck
    # command ahead of it in the same chat stream never resolves at all.
    async def stuck_handler(sender):
        await asyncio.Event().wait()  # never resolves on its own -- only cancellation ends this

    bridge = StuckThenFollowBridge(ModEvent(type="chat", data={"sender": "Alex", "text": "!stuck"}))
    actions = ActionRegistry()
    actions.register(Action(name="stuck", description="", handler=stuck_handler))
    tracker = EntityTracker()
    movement = _movement(bridge, tracker)
    register_movement_actions(actions, movement)

    await asyncio.wait_for(
        run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker()),
        timeout=2.0,
    )

    assert ("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE}) in bridge.sent
    assert "ok, following Alex" in bridge.sent_chat


@pytest.mark.asyncio
async def test_run_loop_survives_a_chat_reply_that_fails_to_send(caplog):
    # Regression test: a live report of "!follow gives no confirmation or
    # error in chat" turned out to have nothing in the log explaining why
    # -- ModBridge._send only logs a dropped command when the connection
    # is None; a .send() call on a connection object that still exists
    # but whose underlying socket is already closing raises instead, and
    # that exception previously had no handler in the run loop at all, so
    # it could propagate out of run() entirely with no attribution to
    # "a chat reply failed to send". The loop must survive this and keep
    # processing later events, with the failure clearly logged.
    class FailingChatBridge(FakeBridge):
        async def send_chat(self, text: str) -> None:
            raise ConnectionError("simulated send failure")

    bridge = FailingChatBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!doesnotexist"}),
        ModEvent(type="chat", data={"sender": "Alex", "text": "hello again"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    with caplog.at_level("ERROR"):
        await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, SelfPositionTracker())

    assert any("failed to send chat reply" in record.message for record in caplog.records)
