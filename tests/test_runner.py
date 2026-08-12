"""Unit tests for minebot/testing/runner.py's TestRunner -- specifically
the "run all" path's stop_on_first_failure behavior (default True, per
explicit direction) since nothing else in the repo exercised TestRunner
directly before.
"""

from __future__ import annotations

import asyncio

import pytest

from minebot.bridge.client import ModEvent
from minebot.bridge.query import QueryResultTracker
from minebot.testing.runner import TestCase, TestContext, TestRegistry, TestRunner


class _GamemodeBridge:
    """Fakes just enough of ModBridge for TestRunner.run's own gamemode
    save/switch/restore (see its own docstring) -- every send_query/
    send_gamemode call immediately queues a matching "survival"
    query_result event, so wait_for_gamemode's own poll loop resolves on
    its first attempt without a real client ever being involved.
    """

    def __init__(self, query_result: QueryResultTracker) -> None:
        self._query_result = query_result

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


def _make_context() -> TestContext:
    query_result = QueryResultTracker()
    bridge = _GamemodeBridge(query_result)
    return TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)


def _make_registry(names_and_outcomes: list[tuple[str, bool]]) -> TestRegistry:
    """Builds a registry of trivial tests that either pass (return
    normally) or fail (raise AssertionError) based on `names_and_outcomes`.
    """
    registry = TestRegistry()
    for name, should_pass in names_and_outcomes:
        async def func(ctx: TestContext, should_pass: bool = should_pass) -> None:
            if not should_pass:
                raise AssertionError("intentional failure")
        registry.register(TestCase(name=name, description="", func=func))
    return registry


@pytest.mark.asyncio
async def test_run_all_stops_at_first_failure_by_default():
    registry = _make_registry([("a", True), ("b", False), ("c", True)])
    runner = TestRunner(registry, _make_context())

    outcomes = await runner.run(None)

    assert [o.name for o in outcomes] == ["a", "b"]
    assert [o.passed for o in outcomes] == [True, False]


@pytest.mark.asyncio
async def test_run_all_continues_past_failures_when_disabled():
    registry = _make_registry([("a", True), ("b", False), ("c", True)])
    runner = TestRunner(registry, _make_context())

    outcomes = await runner.run(None, stop_on_first_failure=False)

    assert [o.name for o in outcomes] == ["a", "b", "c"]
    assert [o.passed for o in outcomes] == [True, False, True]


@pytest.mark.asyncio
async def test_run_all_runs_every_test_when_all_pass():
    registry = _make_registry([("a", True), ("b", True)])
    runner = TestRunner(registry, _make_context())

    outcomes = await runner.run(None)

    assert [o.name for o in outcomes] == ["a", "b"]
    assert all(o.passed for o in outcomes)


@pytest.mark.asyncio
async def test_run_single_named_test_ignores_stop_on_first_failure():
    registry = _make_registry([("a", False)])
    runner = TestRunner(registry, _make_context())

    outcomes = await runner.run("a")

    assert [o.name for o in outcomes] == ["a"]
    assert outcomes[0].passed is False
