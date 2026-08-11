"""Unit tests for the schematic-placement helpers in minebot/testing/
actions.py -- specifically _fill_runs, the pure row-merging logic that
turns a Schematic's individual blocks into /fill-able runs, and
reset_to_idle/query/assert_state, which just need a fake bridge to record
what they sent and a real QueryResultTracker to resolve replies through.
place_schematic/clear_schematic themselves need a real ModBridge and are
exercised by the in-game integration suite instead (tests/integration/),
not here.
"""

from __future__ import annotations

import asyncio

import pytest

from minebot.bridge.client import ModEvent
from minebot.bridge.query import QueryResultTracker
from minebot.testing import actions
from minebot.testing.actions import _fill_runs
from minebot.testing.litematic import Schematic, SchematicBlock
from minebot.testing.runner import TestContext


def test_fill_runs_merges_contiguous_same_block_row():
    schematic = Schematic(
        size_x=3, size_y=1, size_z=1,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=2, y=0, z=0, block="minecraft:stone"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [(0, 0, 0, 2, 0, 0, "minecraft:stone")]


def test_fill_runs_splits_on_block_type_change_and_gap():
    schematic = Schematic(
        size_x=4, size_y=1, size_z=1,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:dirt"),
            # x=2 is missing (a gap) -- must not be merged with x=3.
            SchematicBlock(x=3, y=0, z=0, block="minecraft:dirt"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [
        (0, 0, 0, 0, 0, 0, "minecraft:stone"),
        (1, 0, 0, 1, 0, 0, "minecraft:dirt"),
        (3, 0, 0, 3, 0, 0, "minecraft:dirt"),
    ]


def test_fill_runs_keeps_separate_rows_separate():
    schematic = Schematic(
        size_x=2, size_y=1, size_z=2,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=0, y=0, z=1, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=1, block="minecraft:stone"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [
        (0, 0, 0, 1, 0, 0, "minecraft:stone"),
        (0, 0, 1, 1, 0, 1, "minecraft:stone"),
    ]


def test_fill_runs_empty_schematic():
    assert _fill_runs(Schematic(size_x=1, size_y=1, size_z=1, blocks=[])) == []


class _FakeBridge:
    def __init__(self, query_result: QueryResultTracker | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self._query_result = query_result

    async def send_stop(self) -> None:
        self.sent.append(("stop", {}))

    async def send_query(self, arg: str) -> None:
        self.sent.append(("query", {"arg": arg}))
        # Simulates the mod's real round trip -- MinebotMod.handleQuery
        # replies with a query_result event asynchronously, fast-pathed
        # into QueryResultTracker by run_loop.py's own _read_events (see
        # that module's docstring); here, just deliver it on the next
        # loop iteration so callers awaiting wait_for_next() aren't racing
        # a reply that's already resolved before they start waiting.
        if self._query_result is not None:
            asyncio.get_event_loop().call_soon(
                self._query_result.handle_event,
                ModEvent(type="query_result", data={"arg": arg, "result": "IDLE"}),
            )


@pytest.mark.asyncio
async def test_reset_to_idle_sends_stop():
    bridge = _FakeBridge()
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=None)

    await actions.reset_to_idle(ctx)

    assert bridge.sent == [("stop", {})]


@pytest.mark.asyncio
async def test_query_returns_result_from_reply():
    query_result = QueryResultTracker()
    bridge = _FakeBridge(query_result)
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    result = await actions.query(ctx, "legs")

    assert result == "IDLE"
    assert bridge.sent == [("query", {"arg": "legs"})]


@pytest.mark.asyncio
async def test_query_raises_on_error_reply():
    query_result = QueryResultTracker()

    class _ErrorBridge(_FakeBridge):
        async def send_query(self, arg: str) -> None:
            self.sent.append(("query", {"arg": arg}))
            asyncio.get_event_loop().call_soon(
                query_result.handle_event,
                ModEvent(type="query_result", data={"arg": arg, "error": "unknown query arg: bogus"}),
            )

    ctx = TestContext(bridge=_ErrorBridge(), self_position=None, tracker=None, query_result=query_result)

    with pytest.raises(RuntimeError, match="unknown query arg: bogus"):
        await actions.query(ctx, "bogus")


@pytest.mark.asyncio
async def test_assert_state_passes_when_result_matches():
    query_result = QueryResultTracker()
    bridge = _FakeBridge(query_result)
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    await actions.assert_state(ctx, "legs", "IDLE")  # should not raise


@pytest.mark.asyncio
async def test_assert_state_fails_with_clear_message_when_result_differs():
    query_result = QueryResultTracker()
    bridge = _FakeBridge(query_result)
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    with pytest.raises(AssertionError, match=r"expected legs='GOTO', got 'IDLE'"):
        await actions.assert_state(ctx, "legs", "GOTO")
