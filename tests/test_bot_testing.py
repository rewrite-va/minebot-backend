"""Unit tests for minebot/bot/testing.py's !runtest chat command --
specifically the "all" name / stop_on_first_failure argument parsing,
since TestRunner.run itself is already covered directly in
tests/test_runner.py.
"""

from __future__ import annotations

import asyncio

import pytest

from minebot.actions.registry import ActionRegistry
from minebot.bot.testing import register_testing_actions
from minebot.bridge.client import ModEvent
from minebot.bridge.query import QueryResultTracker
from minebot.testing.runner import TestCase, TestContext, TestRegistry, TestRunner


class _GamemodeBridge:
    """Fakes just enough of ModBridge for TestRunner.run's own gamemode
    save/switch/restore (see tests/test_runner.py's own copy of this
    helper for the fuller docstring) -- every send_query/send_gamemode
    call immediately queues a matching "survival" query_result event.
    Also records every send_chat call -- TestRunner._run_one sends its own
    PASS/FAIL line per test (see its own docstring), which is what these
    tests actually assert on now that !runtest itself replies with nothing
    (see register_testing_actions' own comment for why).
    """

    def __init__(self, query_result: QueryResultTracker) -> None:
        self._query_result = query_result
        self.sent_chat: list[str] = []

    async def send_query(self, arg: str, x: int | None = None, y: int | None = None, z: int | None = None) -> None:
        asyncio.get_event_loop().call_soon(
            self._query_result.handle_event,
            ModEvent(type="query_result", data={"arg": arg, "result": "survival"}),
        )

    async def send_gamemode(self, mode: str) -> None:
        asyncio.get_event_loop().call_soon(
            self._query_result.handle_event,
            ModEvent(type="query_result", data={"arg": "gamemode", "result": mode}),
        )

    async def send_chat(self, message: str) -> None:
        self.sent_chat.append(message)


def _make_context() -> tuple[TestContext, _GamemodeBridge]:
    query_result = QueryResultTracker()
    bridge = _GamemodeBridge(query_result)
    return TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result), bridge


def _make_registry(names_and_outcomes: list[tuple[str, bool]]) -> TestRegistry:
    registry = TestRegistry()
    for name, should_pass in names_and_outcomes:
        async def func(ctx: TestContext, should_pass: bool = should_pass) -> None:
            if not should_pass:
                raise AssertionError("intentional failure")
        registry.register(TestCase(name=name, description="", func=func))
    return registry


@pytest.mark.asyncio
async def test_runtest_with_no_name_stops_at_first_failure():
    registry = _make_registry([("a", True), ("b", False), ("c", True)])
    ctx, bridge = _make_context()
    runner = TestRunner(registry, ctx)
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest", sender="Alex")

    # Each test reports its own PASS/FAIL line via bridge.send_chat as it
    # finishes (see TestRunner._run_one) -- !runtest's own reply is empty
    # (see register_testing_actions' own comment), so the real assertion
    # is against what got sent to chat, not the action's return message.
    assert result.message == ""
    assert bridge.sent_chat[0].startswith("a PASS")
    assert bridge.sent_chat[1].startswith("b FAIL")
    assert len(bridge.sent_chat) == 2  # stopped at the first failure -- "c" never ran


@pytest.mark.asyncio
async def test_runtest_all_false_runs_every_test():
    registry = _make_registry([("a", True), ("b", False), ("c", True)])
    ctx, bridge = _make_context()
    runner = TestRunner(registry, ctx)
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest all false", sender="Alex")

    assert result.message == ""
    assert bridge.sent_chat[0].startswith("a PASS")
    assert bridge.sent_chat[1].startswith("b FAIL")
    assert bridge.sent_chat[2].startswith("c PASS")


@pytest.mark.asyncio
async def test_runtest_named_test_runs_just_that_one():
    registry = _make_registry([("a", True), ("b", False)])
    ctx, bridge = _make_context()
    runner = TestRunner(registry, ctx)
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest b", sender="Alex")

    assert result.message == ""
    assert len(bridge.sent_chat) == 1
    assert bridge.sent_chat[0].startswith("b FAIL")
