import asyncio

import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bot.combat import CombatController, register_combat_actions
from minebot.bot.mining import MiningController, register_mining_actions
from minebot.bot.movement import FOLLOW_STOP_DISTANCE, MovementController, register_movement_actions
from minebot.bot.run_loop import run
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController
from minebot.places import PlaceMemory

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

    async def send_find(self, query, radius=64):
        self.sent.append(("find", {"query": query, "radius": radius}))

    async def send_goto(self, x, y, z, stop_distance=2.0):
        self.sent.append(("goto", {"x": x, "y": y, "z": z, "stop_distance": stop_distance}))

    async def send_dig_down(self, count):
        self.sent.append(("dig_down", {"count": count}))

    async def send_collect(self, query, radius=64):
        self.sent.append(("collect", {"query": query, "radius": radius}))

    async def send_query(self, sub_type, arguments, key):
        self.sent.append(("query", {"sub_type": sub_type, "arguments": arguments, "key": key}))

    async def send_attack(self, query, radius=64):
        self.sent.append(("attack", {"query": query, "radius": radius}))


def _llm(bridge: FakeBridge, actions: ActionRegistry) -> LLMController:
    return LLMController(bridge, actions)  # default NullLLMProvider -- never replies


def _movement(bridge: FakeBridge, tracker: EntityTracker, tmp_path) -> MovementController:
    return MovementController(bridge, tracker, PlaceMemory(tmp_path / "places.json"), SelfPositionTracker())


def _mining(bridge: FakeBridge, inventory: InventoryTracker | None = None) -> MiningController:
    return MiningController(bridge, inventory if inventory is not None else InventoryTracker())


def _combat(bridge: FakeBridge) -> CombatController:
    return CombatController(bridge)


@pytest.mark.asyncio
async def test_run_loop_feeds_entity_events_into_tracker(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 1, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
    ])
    tracker = EntityTracker()
    actions = ActionRegistry()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert tracker.find_by_name("Alex") is not None


@pytest.mark.asyncio
async def test_run_loop_feeds_inventory_events_into_tracker(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": 0, "item": "minecraft:bread", "count": 5}]}),
    ])
    inventory = InventoryTracker()
    tracker = EntityTracker()
    actions = ActionRegistry()

    await run(bridge, actions, tracker, inventory, _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert inventory.count_of("minecraft:bread") == 5


@pytest.mark.asyncio
async def test_run_loop_feeds_position_events_into_self_position_tracker(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="position", data={"x": 10.0, "y": 64.0, "z": -5.0, "yaw": 0.0, "pitch": 0.0}),
    ])
    tracker = EntityTracker()
    actions = ActionRegistry()
    self_position = SelfPositionTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), self_position, _mining(bridge), _combat(bridge))

    assert self_position.current is not None
    assert (self_position.current.x, self_position.current.y, self_position.current.z) == (10.0, 64.0, -5.0)


@pytest.mark.asyncio
async def test_run_loop_dispatches_chat_commands(tmp_path):
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

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert calls == ["Alex"]


@pytest.mark.asyncio
async def test_run_loop_sends_action_result_message_to_chat(tmp_path):
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

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert bridge.sent_chat == ["here's your bread"]


@pytest.mark.asyncio
async def test_run_loop_replies_to_unknown_commands(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "!doesnotexist"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert bridge.sent_chat == ["unknown command: !doesnotexist"]


@pytest.mark.asyncio
async def test_run_loop_does_not_reply_to_unaddressed_non_command_chat(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": "hello there"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_run_loop_routes_bot_addressed_chat_to_the_llm_controller(tmp_path):
    handled = []

    class RecordingLLM:
        async def handle_chat(self, sender, text):
            handled.append((sender, text))

    bridge = FakeBridge([
        ModEvent(type="chat", data={"sender": "Alex", "text": f"hey {BOT_NAME}, got food?"}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), RecordingLLM(), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert handled == [("Alex", f"hey {BOT_NAME}, got food?")]


@pytest.mark.asyncio
async def test_run_loop_routes_trigger_word_chat_to_the_llm_controller(tmp_path):
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

    await run(bridge, actions, tracker, InventoryTracker(), RecordingLLM(), config, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert handled == [("Alex", "hey buddy, got food?")]


@pytest.mark.asyncio
async def test_run_loop_ignores_position_health_death_and_respawn_events_without_crashing(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="hello", data={"commit": "abc123", "built_at": "2026-01-01T00:00:00Z"}),
        ModEvent(type="position", data={"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0, "on_ground": True}),
        ModEvent(type="health", data={"health": 20.0}),
        ModEvent(type="health", data={"health": 0.0}),
        ModEvent(type="death", data={}),
        ModEvent(type="respawn", data={}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))  # should not raise
    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_run_loop_resumes_follow_after_target_reconnects_with_a_new_entity_id(tmp_path):
    # Regression test: a followed player disconnecting and reconnecting
    # gets a brand new entity id on the mod side, but the mod's FOLLOW
    # goal was pinned to the old (now-gone) id -- found live: the bot
    # just stood idle forever after the target rejoined, with no error,
    # until a human retyped !follow. The run loop now watches entity
    # "add" events and re-issues follow via MovementController.
    # on_entity_added whenever the currently-followed name reappears.
    #
    # Asserts on the outcome (ends up following the reconnected id, 42),
    # not the exact wire-command sequence -- chat commands now run as
    # independent concurrent tasks (see run_loop.py's own docstring on
    # why), so there's no longer a guaranteed order between !follow's own
    # _following_name assignment and a same-instant entity-reconnect's
    # on_entity_added check. In the case where on_entity_added's resume
    # loses that race (as it does here, since FakeBridge.events() yields
    # all four canned events with no realistic delay between them), the
    # bot still ends up correctly following 42 -- follow() itself resolves
    # Alex's *current* entity id directly from EntityTracker, which by
    # then already reflects the reconnect. Only the very last "follow"
    # command sent is what matters for correctness, not how many were
    # sent getting there.
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 7, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
        ModEvent(type="chat", data={"sender": "Alex", "text": "!follow"}),
        ModEvent(type="entity", data={"action": "remove", "id": 7}),
        ModEvent(type="entity", data={"action": "add", "id": 42, "name": "Alex", "x": 1.0, "y": 0.0, "z": 1.0}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()
    movement = _movement(bridge, tracker, tmp_path)
    register_movement_actions(actions, movement)

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, movement, SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert bridge.sent
    assert bridge.sent[-1] == ("follow", {"entity_id": 42, "stop_distance": FOLLOW_STOP_DISTANCE})


@pytest.mark.asyncio
async def test_run_loop_does_not_resume_follow_for_an_unrelated_reconnecting_player(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="entity", data={"action": "add", "id": 7, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
        ModEvent(type="chat", data={"sender": "Alex", "text": "!follow"}),
        ModEvent(type="entity", data={"action": "add", "id": 99, "name": "Someone", "x": 0.0, "y": 0.0, "z": 0.0}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()
    movement = _movement(bridge, tracker, tmp_path)
    register_movement_actions(actions, movement)

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, movement, SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert bridge.sent == [("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE})]


@pytest.mark.asyncio
async def test_run_loop_routes_find_result_events_to_movement(tmp_path):
    bridge = FakeBridge([
        ModEvent(type="find_result", data={"query": "cow", "found": False}),
    ])
    actions = ActionRegistry()
    tracker = EntityTracker()
    movement = _movement(bridge, tracker, tmp_path)

    await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, movement, SelfPositionTracker(), _mining(bridge), _combat(bridge))  # should not raise

    assert movement._pending_find is None


class FindReplyBridge(FakeBridge):
    """Only yields the find_result event once send_find has actually been
    called -- mirrors the real mod, which can't reply to a search it
    hasn't received yet (true request/response causality over one
    connection). A plain canned event list can't express this: it would
    let the reader race ahead and observe find_result before the command
    handler has even set up something to receive it, which is impossible
    in the real system (the mod only replies to a query it was sent) and
    was previously masking whether the actual fix works.
    """

    def __init__(self, find_result: ModEvent):
        super().__init__([ModEvent(type="chat", data={"sender": "Alex", "text": "!find cow"})])
        self._find_result = find_result
        self._find_sent = asyncio.Event()
        self._goto_sent = asyncio.Event()

    async def send_find(self, query, radius=64):
        await super().send_find(query, radius)
        self._find_sent.set()

    async def send_goto(self, x, y, z, stop_distance=2.0):
        await super().send_goto(x, y, z, stop_distance)
        self._goto_sent.set()

    async def events(self):
        async for event in super().events():
            yield event
        await self._find_sent.wait()
        yield self._find_result
        if self._find_result.data.get("found"):
            await self._goto_sent.wait()
            yield ModEvent(type="arrived", data={})


@pytest.mark.asyncio
async def test_run_loop_delivers_find_result_without_deadlocking(tmp_path):
    # Regression test: find() suspends the chat handler awaiting a
    # find_result event, and (before the reader/processor split) that
    # suspended the *same* coroutine that reads events off the mod's
    # connection -- so a find_result the mod already sent could never
    # actually be read, and find() always fell through to its 10s timeout
    # no matter how fast the mod replied. This test would hang for the
    # full 10s (and fail via asyncio.wait_for below) if that regressed.
    bridge = FindReplyBridge(
        ModEvent(type="find_result", data={"query": "cow", "found": True, "kind": "entity", "x": 1.0, "y": 2.0, "z": 3.0}),
    )
    actions = ActionRegistry()
    tracker = EntityTracker()
    movement = _movement(bridge, tracker, tmp_path)
    register_movement_actions(actions, movement)

    await asyncio.wait_for(
        run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, movement, SelfPositionTracker(), _mining(bridge), _combat(bridge)),
        timeout=2.0,
    )


class DigDownReplyBridge(FakeBridge):
    """Same request/response-causality shape as FindReplyBridge above, for
    !dig -- dig_down_result is only yielded once send_dig_down has
    actually been called, modeling real mod behavior instead of a plain
    canned event list that could let the reader observe the reply before
    the command that triggers it was ever sent.
    """

    def __init__(self, dig_down_result: ModEvent):
        super().__init__([ModEvent(type="chat", data={"sender": "Alex", "text": "!dig 3"})])
        self._dig_down_result = dig_down_result
        self._dig_down_sent = asyncio.Event()

    async def send_dig_down(self, count):
        await super().send_dig_down(count)
        self._dig_down_sent.set()

    async def events(self):
        async for event in super().events():
            yield event
        await self._dig_down_sent.wait()
        yield self._dig_down_result


@pytest.mark.asyncio
async def test_run_loop_delivers_dig_down_result_without_deadlocking(tmp_path):
    # Same regression as test_run_loop_delivers_find_result_without_deadlocking,
    # for !dig's dig_down_result instead of !find's find_result --
    # dig_down() suspends the chat handler awaiting this event, so
    # _read_events must resolve it (mining.on_dig_down_result) before it
    # ever reaches the processing queue, not after.
    bridge = DigDownReplyBridge(ModEvent(type="dig_down_result", data={"broken": 3}))
    actions = ActionRegistry()
    tracker = EntityTracker()
    mining = _mining(bridge)
    register_mining_actions(actions, mining)

    await asyncio.wait_for(
        run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), mining, _combat(bridge)),
        timeout=2.0,
    )

    assert bridge.sent_chat == ["dug down 3 blocks"]


class CollectReplyBridge(FakeBridge):
    """Same shape as FindReplyBridge/DigDownReplyBridge, for !collect --
    now driven by the query()/query_result round trips (source_for,
    drops_from) and a single collect_result per attempt, replaying real
    request/response causality: each reply is only yielded once the
    corresponding request has actually been sent.
    """

    def __init__(self, source_for_result: list[str], drops_from_result: list[str], collect_results: list[ModEvent], inventory_events: list[ModEvent]):
        super().__init__([ModEvent(type="chat", data={"sender": "Alex", "text": "!collect stone 1"})])
        self._source_for_result = source_for_result
        self._drops_from_result = drops_from_result
        self._collect_results = list(collect_results)
        self._inventory_events = list(inventory_events)
        self._queries_sent: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._collect_sent = asyncio.Event()

    async def send_query(self, sub_type, arguments, key):
        await super().send_query(sub_type, arguments, key)
        await self._queries_sent.put((sub_type, key))

    async def send_collect(self, query, radius=64):
        await super().send_collect(query, radius)
        self._collect_sent.set()

    async def events(self):
        async for event in super().events():
            yield event

        sub_type, key = await self._queries_sent.get()
        assert sub_type == "source_for"
        yield ModEvent(type="query_result", data={"key": key, "result": self._source_for_result})

        sub_type, key = await self._queries_sent.get()
        assert sub_type == "drops_from"
        yield ModEvent(type="query_result", data={"key": key, "result": self._drops_from_result})

        for collect_result, inventory_event in zip(self._collect_results, self._inventory_events):
            self._collect_sent.clear()
            await self._collect_sent.wait()
            yield collect_result
            yield inventory_event


@pytest.mark.asyncio
async def test_run_loop_delivers_collect_result_without_deadlocking(tmp_path):
    # Same regression as the find_result/dig_down_result tests above, for
    # !collect's query_result (source_for/drops_from) and collect_result --
    # collect() suspends awaiting each of these in turn, so _read_events
    # must resolve them before they ever reach the processing queue.
    bridge = CollectReplyBridge(
        source_for_result=["stone"],
        drops_from_result=["minecraft:stone"],
        collect_results=[ModEvent(type="collect_result", data={"success": True})],
        inventory_events=[ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": 0, "item": "minecraft:stone", "count": 1}]})],
    )
    actions = ActionRegistry()
    tracker = EntityTracker()
    inventory = InventoryTracker()
    mining = _mining(bridge, inventory)
    register_mining_actions(actions, mining)

    await asyncio.wait_for(
        run(bridge, actions, tracker, inventory, _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), mining, _combat(bridge)),
        timeout=2.0,
    )

    assert bridge.sent_chat == ["collected 1/1 stone"]


class StuckCollectThenFollowBridge(FakeBridge):
    """Models a !collect that never resolves (its queries get answered, its
    actual collect command goes out, but no collect_result ever arrives --
    the exact live scenario this test guards: a stuck !collect leaving the
    bot unresponsive) followed shortly after by an unrelated !follow chat
    message, mirroring how a player would actually recover from it.
    """

    def __init__(self):
        super().__init__([
            ModEvent(type="entity", data={"action": "add", "id": 7, "name": "Alex", "x": 0.0, "y": 0.0, "z": 0.0}),
            ModEvent(type="chat", data={"sender": "Alex", "text": "!collect stone 10"}),
        ])
        self._queries_sent: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._collect_sent = asyncio.Event()
        self._follow_sent = asyncio.Event()

    async def send_query(self, sub_type, arguments, key):
        await super().send_query(sub_type, arguments, key)
        await self._queries_sent.put((sub_type, key))

    async def send_collect(self, query, radius=64):
        await super().send_collect(query, radius)
        self._collect_sent.set()

    async def send_follow(self, entity_id, stop_distance=2.0):
        await super().send_follow(entity_id, stop_distance)
        self._follow_sent.set()

    async def events(self):
        async for event in super().events():
            yield event

        sub_type, key = await self._queries_sent.get()
        assert sub_type == "source_for"
        yield ModEvent(type="query_result", data={"key": key, "result": ["stone"]})

        sub_type, key = await self._queries_sent.get()
        assert sub_type == "drops_from"
        yield ModEvent(type="query_result", data={"key": key, "result": ["minecraft:stone"]})

        await self._collect_sent.wait()
        # !collect is now genuinely stuck (send_collect went out, but
        # nothing ever answers it) -- this is where a player would type
        # something else after noticing the bot isn't responding.
        yield ModEvent(type="chat", data={"sender": "Alex", "text": "!follow"})
        await self._follow_sent.wait()


@pytest.mark.asyncio
async def test_run_loop_lets_a_new_chat_command_interrupt_a_stuck_one(tmp_path):
    # Regression test: reported live -- !collect stone 10 got stuck with
    # no reply (the mod-side collect loop never sent a result back), and
    # every command typed after it (!follow, !stop, ...) silently did
    # nothing because the old run loop awaited each chat command fully
    # before looking at the next one, so they just sat queued behind the
    # stuck collect() call for its full multi-minute inactivity timeout.
    # Chat commands now run as independent, cancellable tasks (see
    # run_loop.py's own docstring) -- a new command cancels whatever
    # command is still running instead of waiting for it, so !follow here
    # must get dispatched and replied to promptly even though the !collect
    # ahead of it in the same chat stream never resolves at all.
    bridge = StuckCollectThenFollowBridge()
    actions = ActionRegistry()
    tracker = EntityTracker()
    mining = _mining(bridge)
    register_mining_actions(actions, mining)
    movement = _movement(bridge, tracker, tmp_path)
    register_movement_actions(actions, movement)

    await asyncio.wait_for(
        run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, movement, SelfPositionTracker(), mining, _combat(bridge)),
        timeout=2.0,
    )

    assert ("follow", {"entity_id": 7, "stop_distance": FOLLOW_STOP_DISTANCE}) in bridge.sent
    assert "ok, following Alex" in bridge.sent_chat
    # The stuck collect's own pending-future bookkeeping must have been
    # cleaned up by cancellation (its `finally` block), not left dangling.
    assert mining._pending_collect_result is None


@pytest.mark.asyncio
async def test_run_loop_survives_a_chat_reply_that_fails_to_send(tmp_path, caplog):
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
        await run(bridge, actions, tracker, InventoryTracker(), _llm(bridge, actions), CONFIG, _movement(bridge, tracker, tmp_path), SelfPositionTracker(), _mining(bridge), _combat(bridge))

    assert any("failed to send chat reply" in record.message for record in caplog.records)
