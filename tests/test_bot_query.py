"""Integration-ish unit test for !query -- registers the real action
(minebot/bot/query.py) and drives it through run_loop.run() end to end
with a fake bridge, confirming the chat command actually sends
{"type": "query", "arg": ...} and replies with the mod's own result once
a matching query_result event arrives.
"""

from __future__ import annotations

import asyncio

import pytest

from minebot.actions.registry import ActionRegistry
from minebot.bot.combat import CombatController
from minebot.bot.player_intention import PlayerIntentionController
from minebot.bot.query import register_query_action
from minebot.bot.run_loop import run
from minebot.bot.self_defense import SelfDefenseTrigger
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.query import QueryResultTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController

CONFIG = BotConfig(
    mod_host="0.0.0.0", mod_port=0, bot_name="minebot", trigger_words=(), mod_repo_path=None,
    observer_host="0.0.0.0", observer_port=0,
)


class FakeBridge:
    """Replays an initial canned event (the !query chat command itself),
    then behaves like the real mod: send_query() pushes a reply event onto
    the SAME stream events() yields from, so the reply is guaranteed to
    arrive strictly after the query was sent -- matching the real
    ordering guarantee a real WebSocket round trip has, unlike a canned
    event list where a later item could otherwise race the handler that's
    supposed to be waiting for it.
    """

    def __init__(self, initial_events: list[ModEvent], query_replies: dict[str, ModEvent]):
        self._queue: asyncio.Queue[ModEvent] = asyncio.Queue()
        for event in initial_events:
            self._queue.put_nowait(event)
        self._query_replies = query_replies
        self.sent_chat: list[str] = []
        self.sent_queries: list[str] = []

    async def events(self):
        while True:
            yield await self._queue.get()

    async def send_chat(self, text: str) -> None:
        self.sent_chat.append(text)

    async def send_query(self, arg: str) -> None:
        self.sent_queries.append(arg)
        await self._queue.put(self._query_replies[arg])


@pytest.mark.asyncio
async def test_query_command_reports_the_mods_result():
    bridge = FakeBridge(
        initial_events=[ModEvent(type="chat", data={"sender": "Alex", "text": "!query legs"})],
        query_replies={"legs": ModEvent(type="query_result", data={"arg": "legs", "result": "GOTO"})},
    )
    actions = ActionRegistry()
    query_result = QueryResultTracker()
    register_query_action(actions, bridge, query_result)
    tracker = EntityTracker()
    combat = CombatController(bridge, tracker, PlayerIntentionController())
    self_defense = SelfDefenseTrigger(combat, combat.intention)

    run_task = asyncio.ensure_future(run(
        bridge, actions, tracker, InventoryTracker(), LLMController(bridge, actions), CONFIG,
        SelfPositionTracker(), self_defense, query_result,
    ))
    await asyncio.sleep(0.1)  # let the chat command dispatch, send, and get its reply
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    assert bridge.sent_queries == ["legs"]
    assert bridge.sent_chat == ["legs = GOTO"]


@pytest.mark.asyncio
async def test_query_command_reports_mod_side_errors():
    bridge = FakeBridge(
        initial_events=[ModEvent(type="chat", data={"sender": "Alex", "text": "!query bogus"})],
        query_replies={"bogus": ModEvent(type="query_result", data={"arg": "bogus", "error": "unknown query arg: bogus"})},
    )
    actions = ActionRegistry()
    query_result = QueryResultTracker()
    register_query_action(actions, bridge, query_result)
    tracker = EntityTracker()
    combat = CombatController(bridge, tracker, PlayerIntentionController())
    self_defense = SelfDefenseTrigger(combat, combat.intention)

    run_task = asyncio.ensure_future(run(
        bridge, actions, tracker, InventoryTracker(), LLMController(bridge, actions), CONFIG,
        SelfPositionTracker(), self_defense, query_result,
    ))
    await asyncio.sleep(0.1)
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    assert bridge.sent_chat == ["query error: unknown query arg: bogus"]
