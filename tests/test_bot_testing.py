"""Unit tests for minebot/bot/testing.py's !runtest chat command --
specifically the "all" name / stop_on_first_failure argument parsing,
since TestRunner.run itself is already covered directly in
tests/test_runner.py.
"""

from __future__ import annotations

import pytest

from minebot.actions.registry import ActionRegistry
from minebot.bot.testing import register_testing_actions
from minebot.testing.runner import TestCase, TestContext, TestRegistry, TestRunner


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
    runner = TestRunner(registry, TestContext(bridge=None, self_position=None, tracker=None, query_result=None))
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest", sender="Alex")

    assert "1/2 passed" in result.message
    assert "PASS a" in result.message
    assert "FAIL b" in result.message
    assert "c" not in result.message.split("--", 1)[1]


@pytest.mark.asyncio
async def test_runtest_all_false_runs_every_test():
    registry = _make_registry([("a", True), ("b", False), ("c", True)])
    runner = TestRunner(registry, TestContext(bridge=None, self_position=None, tracker=None, query_result=None))
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest all false", sender="Alex")

    assert "2/3 passed" in result.message
    assert "PASS a" in result.message
    assert "FAIL b" in result.message
    assert "PASS c" in result.message


@pytest.mark.asyncio
async def test_runtest_named_test_runs_just_that_one():
    registry = _make_registry([("a", True), ("b", False)])
    runner = TestRunner(registry, TestContext(bridge=None, self_position=None, tracker=None, query_result=None))
    actions = ActionRegistry()
    register_testing_actions(actions, runner)

    result = await actions.dispatch_chat("!runtest b", sender="Alex")

    assert "0/1 passed" in result.message
    assert "FAIL b" in result.message
