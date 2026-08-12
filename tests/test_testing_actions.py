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
    # through the path waypoint block's own CENTER (2.5, 0.5, 0.5, see
    # goto_with_waypoints' own WAYPOINT_RADIUS docstring for why hits are
    # checked against a waypoint's real block center, not its raw
    # minimum-corner integer coordinate) and nowhere near the forbidden
    # one at x=10, ending within tolerance of the target at x=4.
    steps = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.5, 0.5, 0.5), (3.0, 0.0, 0.0), (4.0, 0.0, 0.0)]
    bridge = _GotoBridge(self_position, steps)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    result = await actions.goto_with_waypoints(
        ctx, target_x=4.0, target_y=0.0, target_z=0.0, distance_tolerance=0.5, timeout=2.0,
        path=[Waypoint(x=2, y=0, z=0)],
        forbidden=[Waypoint(x=10, y=0, z=0)],
    )

    # Per GotoWaypointResult's own docstring, a test only ever asserts
    # `> 0`/`== 0` -- how many segments land inside the radius is
    # incidental to sampling/interpolation, not something to pin an exact
    # count to.
    assert result.path_hits[0] > 0
    assert result.forbidden_hits == [0]


@pytest.mark.asyncio
async def test_goto_with_waypoints_uses_real_3d_distance_for_forbidden_hits():
    # Regression test, per direct live report: the bot correctly jumped
    # OVER a gap (never actually entering the forbidden cell at the gap's
    # own foot height), but the test still failed with "fell into
    # forbidden waypoint" because the old hit-detection only checked
    # (x, z), ignoring y entirely -- passing directly ABOVE a forbidden
    # column at a different height falsely counted as a hit.
    #
    # Flies at y=3 over a forbidden cell centered at y=1.5 -- 1.5 blocks of
    # vertical clearance, deliberately well outside WAYPOINT_RADIUS (0.5)
    # even measured against the straight-line CHORD between consecutive
    # samples (not just the samples themselves -- see
    # goto_with_waypoints' own _segment_hits_sphere docstring for why hits
    # are checked against the segment between ticks, not just each landed-
    # on point: a real jump's y=2/y=1.5 clearance from the older version of
    # this test was too marginal to tell "flew over" from "a segment
    # between two samples swept transiently closer to the cell than either
    # endpoint did", which is real/correct geometry, not a bug).
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": 3.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    # Walk straight across z at a CONSTANT y=3 (a clean jump over the
    # gap), passing directly over the forbidden waypoint's own (x, z)
    # column at z=1 but never actually descending to its real y=1.
    steps = [(0.0, 3.0, 0.0), (0.0, 3.0, 1.0), (0.0, 3.0, 2.0)]
    bridge = _GotoBridge(self_position, steps)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    result = await actions.goto_with_waypoints(
        ctx, target_x=0.0, target_y=3.0, target_z=2.0, distance_tolerance=0.5, timeout=2.0,
        forbidden=[Waypoint(x=0, y=1, z=1)],  # the gap itself, at foot height -- never actually entered
    )

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


@pytest.mark.asyncio
async def test_count_jumps_counts_a_real_liftoff_land_cycle():
    self_position = SelfPositionTracker()

    async def _during() -> None:
        for x, y, on_ground in [
            (0.0, 0.0, True),
            (0.5, 0.3, False),
            (1.0, 0.5, False),
            (1.5, 0.2, False),
            (2.0, 0.0, True),
        ]:
            self_position.handle_event(
                ModEvent(type="position", data={"x": x, "y": y, "z": 0.0, "yaw": 0.0, "pitch": 0.0, "on_ground": on_ground})
            )

    ctx = TestContext(bridge=None, self_position=self_position, tracker=None, query_result=None)
    jumps = await actions.count_jumps(ctx, during=_during())

    assert jumps == 1


@pytest.mark.asyncio
async def test_count_jumps_ignores_ground_contact_flicker():
    # Regression test, per direct live report: goto_leaves_2 fired exactly
    # ONE real jump (confirmed via the mod's own navigate[diag] log,
    # jump=true exactly once) but count_jumps reported 3 -- because
    # MinebotMod's position broadcast dedup deliberately excludes
    # on_ground (see MinebotMod.maybeBroadcastPositionEvent's own
    # docstring), so vanilla's own known onGround() flicker at a block
    # edge rides along on ordinary movement broadcasts as spurious
    # on_ground=false ticks with no real height gained at all.
    self_position = SelfPositionTracker()

    async def _during() -> None:
        for x, y, on_ground in [
            (0.0, -60.0, True),
            (0.3, -60.0, False),  # flicker: no real height gained
            (0.6, -60.0, True),
            (0.9, -60.0, False),  # flicker: no real height gained
            (1.2, -60.0, True),
        ]:
            self_position.handle_event(
                ModEvent(type="position", data={"x": x, "y": y, "z": 0.0, "yaw": 0.0, "pitch": 0.0, "on_ground": on_ground})
            )

    ctx = TestContext(bridge=None, self_position=self_position, tracker=None, query_result=None)
    jumps = await actions.count_jumps(ctx, during=_during())

    assert jumps == 0


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


class _SameColumnDifferentHeightBridge:
    """send_goto moves self_position to the SAME (x, z) column as the
    target but leaves y untouched -- models a real live bug: an
    "unreachable" target sitting atop a blocking wall shares its (x, z)
    with the wall's own base, so a bot merely standing at the foot of the
    wall (far below the real target) must NOT count as having reached it.
    """

    def __init__(self, self_position: SelfPositionTracker, stuck_y: float) -> None:
        self._self_position = self_position
        self._stuck_y = stuck_y

    async def send_goto(self, x: float, y: float, z: float) -> None:
        self._self_position.handle_event(
            ModEvent(type="position", data={"x": x, "y": self._stuck_y, "z": z, "yaw": 0.0, "pitch": 0.0})
        )


@pytest.mark.asyncio
async def test_assert_goto_never_arrives_uses_real_3d_distance_not_just_horizontal():
    # Regression test: assert_goto_never_arrives used to check only
    # (x, z), the same horizontal-only semantics goto() itself uses (see
    # its own docstring) -- correct for "walk to this reachable spot",
    # but wrong for "unreachable": a target 10 blocks straight up from
    # where the bot is stuck must not register as reached.
    self_position = SelfPositionTracker()
    self_position.handle_event(ModEvent(type="position", data={"x": 0.0, "y": -60.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}))
    bridge = _SameColumnDifferentHeightBridge(self_position, stuck_y=-60.0)
    ctx = TestContext(bridge=bridge, self_position=self_position, tracker=None, query_result=None)

    # Should return cleanly, no exception -- the bot lands on the exact
    # same (x, z) column as the target but 10 blocks below it in y.
    await actions.assert_goto_never_arrives(
        ctx, target_x=0.0, target_y=-50.0, target_z=0.0, distance_tolerance=0.5, timeout=0.3,
    )


class _PositionQueryBridge:
    """Replies to send_query("position") with the real nested "position"
    object shape MinebotMod.handleQuery sends (not the plain "result"
    string shape every other query arg uses) -- see actions.query_position's
    own docstring for why position needs its own reply shape.
    """

    def __init__(self, query_result: QueryResultTracker, position: dict | None = None, error: str | None = None) -> None:
        self._query_result = query_result
        self._position = position
        self._error = error

    async def send_query(self, arg: str) -> None:
        data = {"arg": arg}
        if self._error is not None:
            data["error"] = self._error
        else:
            data["position"] = self._position
        asyncio.get_event_loop().call_soon(
            self._query_result.handle_event, ModEvent(type="query_result", data=data),
        )


@pytest.mark.asyncio
async def test_query_position_returns_real_live_position():
    query_result = QueryResultTracker()
    bridge = _PositionQueryBridge(
        query_result, position={"x": 1.5, "y": -60.0, "z": 2.5, "yaw": 90.0, "pitch": -10.0},
    )
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    position = await actions.query_position(ctx)

    assert (position.x, position.y, position.z) == (1.5, -60.0, 2.5)
    assert (position.yaw, position.pitch) == (90.0, -10.0)


@pytest.mark.asyncio
async def test_query_position_raises_on_error_reply():
    query_result = QueryResultTracker()
    bridge = _PositionQueryBridge(query_result, error="no local player yet")
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    with pytest.raises(RuntimeError, match="no local player yet"):
        await actions.query_position(ctx)


class _BlockQueryBridge:
    """Replies to send_query("block", x, y, z) with the real "block"/
    "loaded" reply shape MinebotMod.handleQuery sends -- see
    actions.query_block's own docstring.
    """

    def __init__(self, query_result: QueryResultTracker, block: str | None = None, loaded: bool = True, error: str | None = None) -> None:
        self._query_result = query_result
        self._block = block
        self._loaded = loaded
        self._error = error
        self.sent: list[tuple[str, int, int, int]] = []

    async def send_query(self, arg: str, x: int | None = None, y: int | None = None, z: int | None = None) -> None:
        self.sent.append((arg, x, y, z))
        data = {"arg": arg}
        if self._error is not None:
            data["error"] = self._error
        else:
            data["block"] = self._block
            data["loaded"] = self._loaded
        asyncio.get_event_loop().call_soon(
            self._query_result.handle_event, ModEvent(type="query_result", data=data),
        )


@pytest.mark.asyncio
async def test_query_block_returns_real_block_id():
    query_result = QueryResultTracker()
    bridge = _BlockQueryBridge(query_result, block="minecraft:stone", loaded=True)
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    block = await actions.query_block(ctx, 0, -60, 0)

    assert block == "minecraft:stone"
    assert bridge.sent == [("block", 0, -60, 0)]


@pytest.mark.asyncio
async def test_query_block_raises_on_error_reply():
    query_result = QueryResultTracker()
    bridge = _BlockQueryBridge(query_result, error="block query requires x/y/z and a loaded level")
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    with pytest.raises(RuntimeError, match="block query requires"):
        await actions.query_block(ctx, 0, -60, 0)


class _TeleportBridge:
    """send_console_command sends a real /tp; send_query("position")
    replies with the real target position but only after `slow_replies`
    query attempts are silently dropped first (never resolved) -- models a
    single slow/lost query round trip without failing the whole call, the
    exact regression this covers (see actions.POLL_QUERY_TIMEOUT_SECONDS's
    own docstring): a caller polling in a loop must retry a single missed
    reply, not treat it as a hard failure of the whole operation.
    """

    def __init__(self, query_result: QueryResultTracker, target: tuple[float, float, float], slow_replies: int) -> None:
        self._query_result = query_result
        self._target = target
        self._slow_replies = slow_replies
        self._query_count = 0
        self.sent_commands: list[str] = []

    async def send_console_command(self, text: str) -> None:
        self.sent_commands.append(text)

    async def send_teleport(self, x: float, y: float, z: float) -> None:
        self.sent_commands.append(f"/tp @s {x} {y} {z}")

    async def send_query(self, arg: str, x: int | None = None, y: int | None = None, z: int | None = None) -> None:
        self._query_count += 1
        if self._query_count <= self._slow_replies:
            return  # dropped -- simulates a reply that never arrives in time for this attempt
        x_, y_, z_ = self._target
        position = {"x": x_, "y": y_, "z": z_, "yaw": 0.0, "pitch": 0.0}
        asyncio.get_event_loop().call_soon(
            self._query_result.handle_event,
            ModEvent(type="query_result", data={"arg": arg, "position": position}),
        )


@pytest.mark.asyncio
async def test_teleport_retries_a_single_slow_query_reply_instead_of_failing():
    # Regression test: teleport()'s own polling loop used to pass the
    # SAME timeout to each individual query_position() call as the whole
    # teleport() call's own outer timeout -- a single slow/lost reply
    # could eat the entire outer budget before a second poll attempt ever
    # ran. Here the first query attempt is dropped (simulating that slow
    # reply); teleport() must retry and still succeed well within its own
    # timeout, not raise on the first missed attempt.
    query_result = QueryResultTracker()
    bridge = _TeleportBridge(query_result, target=(1.0, 2.0, 3.0), slow_replies=1)
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None, query_result=query_result)

    await actions.teleport(ctx, 1.0, 2.0, 3.0, timeout=5.0)

    assert bridge.sent_commands == ["/tp @s 1.0 2.0 3.0"]
    assert bridge._query_count >= 2
