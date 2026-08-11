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
from minebot.bridge.self_position import SelfPositionTracker
from minebot.testing import actions
from minebot.testing.actions import _fill_runs
from minebot.testing.litematic import Schematic, SchematicBlock, Waypoint
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


class _GotoBridge:
    """Drives self_position through a canned walk (a straight line from
    (0,0,0) toward the target, one position event per step) as soon as
    send_goto is called -- lets goto_with_waypoints' own position listener
    see a real trail of samples to check against path/forbidden waypoints,
    the same way it would consume real broadcast `position` events.
    """

    def __init__(self, self_position: SelfPositionTracker, steps: list[tuple[float, float, float]]):
        self._self_position = self_position
        self._steps = steps

    async def send_goto(self, x: float, y: float, z: float) -> None:
        for sx, sy, sz in self._steps:
            self._self_position.handle_event(
                ModEvent(type="position", data={"x": sx, "y": sy, "z": sz, "yaw": 0.0, "pitch": 0.0})
            )


@pytest.mark.asyncio
async def test_goto_with_waypoints_counts_path_and_forbidden_hits():
    self_position = SelfPositionTracker()
    # Bot's own real broadcast position first (goto's own initial-position
    # wait needs at least one before it'll send anything).
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    # A straight walk along x: 0 -> 1 -> 2 -> 3 -> 4, passing directly
    # through the path waypoint at x=2 and nowhere near the forbidden one
    # at x=10, ending within tolerance of the target at x=4.
    steps = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (4.0, 0.0, 0.0)]
    bridge = _GotoBridge(self_position, steps)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    result = await actions.goto_with_waypoints(
        ctx, target_x=4.0, target_y=0.0, target_z=0.0, distance_tolerance=0.5, timeout=2.0,
        path=[Waypoint(x=2, y=0, z=0)],
        forbidden=[Waypoint(x=10, y=0, z=0)],
    )

    assert result.path_hits == [1]
    assert result.forbidden_hits == [0]


@pytest.mark.asyncio
async def test_goto_with_waypoints_removes_its_listener_after_returning():
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    steps = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    bridge = _GotoBridge(self_position, steps)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    await actions.goto_with_waypoints(
        ctx, target_x=1.0, target_y=0.0, target_z=0.0, distance_tolerance=0.5, timeout=2.0,
    )

    # No listener should still be registered -- a leftover one would keep
    # tagging hits for whatever the NEXT test's own walk does.
    assert self_position._listeners == []


class _StaticGotoBridge:
    """send_goto is a no-op -- self_position never moves, simulating a
    genuinely blocked target the bot can never actually reach.
    """

    async def send_goto(self, x: float, y: float, z: float) -> None:
        pass


@pytest.mark.asyncio
async def test_assert_goto_never_arrives_passes_when_bot_stays_away():
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    ctx = TestContext(bridge=_StaticGotoBridge(), self_position=self_position, tracker=None, query_result=None)

    # Should return cleanly, no exception -- the bot (frozen at (0,0,0) by
    # _StaticGotoBridge's own no-op send_goto) never comes within
    # distance_tolerance of a target 100 blocks away.
    await actions.assert_goto_never_arrives(
        ctx, target_x=100.0, target_y=0.0, target_z=100.0, distance_tolerance=0.5, timeout=0.3,
    )


class _ArrivingGotoBridge:
    """send_goto immediately jumps self_position to the target -- models
    the failure case: the supposedly-unreachable target IS actually
    reached.
    """

    def __init__(self, self_position: SelfPositionTracker) -> None:
        self._self_position = self_position

    async def send_goto(self, x: float, y: float, z: float) -> None:
        self._self_position.handle_event(
            ModEvent(type="position", data={"x": x, "y": y, "z": z, "yaw": 0.0, "pitch": 0.0})
        )


@pytest.mark.asyncio
async def test_assert_goto_never_arrives_fails_when_bot_actually_arrives():
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    bridge = _ArrivingGotoBridge(self_position)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    with pytest.raises(AssertionError, match="reached supposedly unreachable target"):
        await actions.assert_goto_never_arrives(
            ctx, target_x=5.0, target_y=0.0, target_z=5.0, distance_tolerance=0.5, timeout=1.0,
        )
