"""Reusable, self-timing-out test primitives -- send a goal via
`ctx.bridge`, then poll tracked state until it holds or a real deadline
passes, raising on timeout rather than looping forever.

Exists because a bare test body polling in a `while True` loop with no
timeout of its own is only safe to call through TestRunner (see runner.py),
which wraps every test in `asyncio.wait_for`. The pytest integration
driver (`minebot` repo's `tests/integration/`) calls test bodies directly,
with no such wrapper -- confirmed live: a test written as an unbounded
poll loop hung the whole pytest run indefinitely once its own arrival
tolerance was tightened past what the mod could actually satisfy, with no
error, no timeout, nothing to signal what was wrong short of noticing the
process never returned. Every primitive here takes its own explicit
`timeout` and raises `asyncio.TimeoutError` (a real, standard, assertable
exception) if the condition never holds -- safe to call from ANY caller,
manual `!runtest` or pytest, with no dependency on an outer wrapper
providing the bound.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from minebot.bridge.self_position import SelfPosition
from minebot.testing.litematic import Schematic, Waypoint
from minebot.testing.runner import TestContext

POLL_INTERVAL_SECONDS = 0.5
# A generous, separate budget for "the world has finished loading and the
# player entity exists at all" -- kept OUT of goto()/teleport()'s own
# `timeout` parameter (see their own docstrings for the real bug this
# fixes: a caller-supplied timeout meant for "how long should the actual
# action take" was previously also covering this initial wait, so a slow
# world load could eat most of a tight timeout before the real action
# even started, leaving the action itself almost no real budget despite
# succeeding well within its own fair time).
INITIAL_POSITION_TIMEOUT_SECONDS = 30.0


async def wait_for_position(ctx: TestContext, timeout: float) -> SelfPosition:
    """Waits for the first real `position` broadcast and returns it --
    needed before a test can even DECIDE what to send when a target is
    computed relative to the bot's own current position (as
    test_goto_moves_bot_to_target's own target is). `hello` (which
    callers wait for to know the control channel is up -- see conftest.py)
    fires the instant ControlClient connects, well BEFORE the world has
    actually finished loading and the player entity exists to report a
    position at all -- confirmed live, twice: a caller that read
    ctx.self_position.current synchronously right after `hello` failed
    instantly both times a test needed a real starting position, since
    quickplay/world-bootstrap hadn't actually joined the world yet. Raises
    `asyncio.TimeoutError` (same shape as goto() below) if no position
    ever arrives within `timeout` -- a real, clear failure instead of a
    silent hang, rather than looping forever the way an earlier version
    of this wait did when called with no wrapper around it.
    """
    async def _wait() -> SelfPosition:
        while ctx.self_position.current is None:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        return ctx.self_position.current

    return await asyncio.wait_for(_wait(), timeout=timeout)


async def _wait_for_initial_position(ctx: TestContext) -> None:
    """Internal: goto()/teleport() both need a real position to exist
    before they can do anything at all, but that initial wait must NOT
    consume the caller's own `timeout` budget for the actual action (see
    INITIAL_POSITION_TIMEOUT_SECONDS's own comment for the real bug this
    fixes) -- so this uses its own fixed, generous timeout instead of
    whatever the caller passed in for the action itself.
    """
    await wait_for_position(ctx, timeout=INITIAL_POSITION_TIMEOUT_SECONDS)


async def goto(ctx: TestContext, target_x: float, target_y: float, target_z: float, distance_tolerance: float, timeout: float) -> None:
    """Sends `!goto` to (target_x, target_y, target_z) and waits for the
    bot's live (x, z) to come within `distance_tolerance` of it (y is not
    checked -- see LegsGotoNode's own arrival check, which is also
    horizontal-only, matching real player movement having no independent
    vertical stop condition). Raises `asyncio.TimeoutError` if that never
    happens within `timeout` seconds -- callers should let this propagate
    (a pytest test simply fails with a clear TimeoutError instead of
    hanging; !runtest's own TestRunner catches it the same way it catches
    any other exception, see runner.py's own docstring) rather than
    catching and swallowing it.

    `distance_tolerance` is a parameter, not a hardcoded constant, since
    what counts as "close enough" is a property of what the CALLER is
    testing, not something this shared helper should assume -- confirmed
    live: an earlier version hardcoded a tolerance here, and it silently
    stopped matching once the mod's own real arrival precision changed
    (see LegsGotoNode's own ARRIVAL_DISTANCE, currently 0.5 blocks) --
    passing it explicitly at the call site keeps the test's actual
    expectation visible in the test itself, not buried in a shared
    constant two files away.
    """
    await _wait_for_initial_position(ctx)

    async def _wait_for_arrival() -> None:
        await ctx.bridge.send_goto(target_x, target_y, target_z)

        while True:
            pos = ctx.self_position.current
            if pos is not None:
                distance = math.dist((pos.x, pos.z), (target_x, target_z))
                if distance <= distance_tolerance:
                    return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    await asyncio.wait_for(_wait_for_arrival(), timeout=timeout)


@dataclass
class GotoWaypointResult:
    __test__ = False  # not a pytest test class -- matches TestContext's own opt-out (see runner.py)

    # How many real `position` events (not polled samples -- every one the
    # mod actually broadcast while walking) landed within a real 3D
    # WAYPOINT_RADIUS of each yellow "path" waypoint's own (x, y, z)
    # position, keyed by index into the Waypoints.path list passed in --
    # a test asserts `> 0` for every index it expects the bot to have
    # actually walked through (per explicit direction: "assert if it is
    # gt 0", not an exact count, since how many ticks a real walk spends
    # inside a given radius is incidental to movement speed/timing, not
    # something a test should pin an exact number to).
    path_hits: list[int]
    # Same shape, for red "forbidden" waypoints -- a test asserts `== 0`
    # for every index it expects the bot to have avoided entirely.
    forbidden_hits: list[int]


# How close a real 3D (x, y, z) position has to come to a waypoint's own
# position to count as "the bot was at this waypoint" -- matches
# GOTO_ARRIVAL_TOLERANCE's own reasoning (see tests.py) rather than
# requiring an exact block match, since real per-tick movement won't land
# on an exact integer coordinate most ticks. Deliberately a real 3D
# distance, NOT horizontal-only (x, z) -- found live, per direct report: a
# forbidden waypoint marking a gap's own foot-height column falsely
# registered as "hit" while the bot correctly jumped OVER the gap at a
# higher y, since an (x, z)-only check can't tell "walked through this
# exact cell" from "passed directly above/below it on the way to a
# genuinely different height". goto()'s own arrival check is still
# deliberately horizontal-only (see its own docstring) -- that's a
# different concept ("reached this x/z, whatever height it settles at",
# matching real player movement having no independent vertical stop
# condition), not "occupied this exact 3D cell", which is what a
# path/forbidden waypoint actually means.
WAYPOINT_RADIUS = 0.75


def _segment_hits_sphere(
    start: tuple[float, float, float], end: tuple[float, float, float], center: tuple[float, float, float], radius: float,
) -> bool:
    """True if the line segment start->end passes within `radius` of
    `center` at any point along it, not just at its two endpoints --
    standard point-to-segment closest-distance check (clamp the
    projection of `center` onto the segment to [0, 1], measure from
    there). `start == end` degrades to a plain point check, which is what
    the very first `position` broadcast of a walk (no previous sample to
    form a segment from) naturally produces.
    """
    seg = tuple(e - s for s, e in zip(start, end))
    seg_len_sq = sum(c * c for c in seg)
    if seg_len_sq == 0:
        return math.dist(start, center) <= radius
    to_center = tuple(c - s for s, c in zip(start, center))
    t = max(0.0, min(1.0, sum(a * b for a, b in zip(to_center, seg)) / seg_len_sq))
    closest = tuple(s + t * d for s, d in zip(start, seg))
    return math.dist(closest, center) <= radius


# Checked against each waypoint's BLOCK CENTER (x+0.5, y+0.5, z+0.5), not
# its raw minimum-corner (x, y, z) -- confirmed live as a real bug: a
# physics-correct, close-but-not-exact jump landed at real distance ~1.16
# blocks from a path waypoint's own raw corner coordinate but only ~0.43
# blocks from that same block's real center, well inside WAYPOINT_RADIUS.
# litematic.Waypoint intentionally stores the same minimum-corner integer
# coordinates as SchematicBlock (see its own docstring) since that's the
# natural representation for offsetting by an anchor -- but "was the bot
# at this waypoint" is a real-world proximity question, and a block's
# real center is what a bot actually walking through/near it will get
# close to, not its arbitrary corner.


async def goto_with_waypoints(
    ctx: TestContext,
    target_x: float, target_y: float, target_z: float,
    distance_tolerance: float,
    timeout: float,
    path: list[Waypoint] = (),
    forbidden: list[Waypoint] = (),
) -> GotoWaypointResult:
    """Same send-!goto-and-wait-for-arrival shape as goto() above, but also
    watches every real `position` event broadcast WHILE walking (not a
    coarse poll -- see SelfPositionTracker.add_listener's own docstring
    for why a listener callback is needed here instead of just reading
    `.current` periodically) and tags each one against every waypoint in
    `path`/`forbidden` (both `litematic.Waypoint` lists, already offset to
    real world coordinates by the caller -- see tests.py's own call site
    for the anchor-offset math) -- built specifically for the wool-marker
    workflow (litematic.WAYPOINT_BLOCK_ROLES): a scenario built visually
    in Litematica can now assert the bot's real walked trail actually
    passed through every yellow block and never touched a red one, not
    just that it eventually arrived at the green one.

    Returns a GotoWaypointResult with per-waypoint hit counts rather than
    asserting anything itself -- what counts as a passing test (`path_hits[i]
    > 0`, `forbidden_hits[i] == 0`, or something looser) is a property of
    what the CALLER is testing, same reasoning distance_tolerance is a
    parameter on goto() rather than a hardcoded assumption here.
    """
    await _wait_for_initial_position(ctx)

    path_hits = [0] * len(path)
    forbidden_hits = [0] * len(forbidden)
    # Tracks the previous sampled position so each new broadcast can be
    # checked as a SEGMENT (prev -> current), not just a point -- see
    # _segment_hits_sphere's own docstring for why a fast jump's own real
    # arc can step clean past a waypoint's WAYPOINT_RADIUS sphere between
    # two consecutive `position` broadcasts (confirmed live: goto_jump_1
    # flaked with "never walked through path waypoint" on a jump that
    # landed correctly, because the two ticks straddling the waypoint's
    # own (x, z) column happened to both fall just outside the point-radius
    # check even though the straight-line arc between them passed through
    # it).
    prev_pos: SelfPosition | None = None

    def _on_position(pos: SelfPosition) -> None:
        nonlocal prev_pos
        start = (prev_pos.x, prev_pos.y, prev_pos.z) if prev_pos is not None else (pos.x, pos.y, pos.z)
        end = (pos.x, pos.y, pos.z)
        for i, waypoint in enumerate(path):
            center = (waypoint.x + 0.5, waypoint.y + 0.5, waypoint.z + 0.5)
            if _segment_hits_sphere(start, end, center, WAYPOINT_RADIUS):
                path_hits[i] += 1
        for i, waypoint in enumerate(forbidden):
            center = (waypoint.x + 0.5, waypoint.y + 0.5, waypoint.z + 0.5)
            if _segment_hits_sphere(start, end, center, WAYPOINT_RADIUS):
                forbidden_hits[i] += 1
        prev_pos = pos

    ctx.self_position.add_listener(_on_position)
    try:
        await goto(ctx, target_x, target_y, target_z, distance_tolerance, timeout)
    finally:
        # Removed unconditionally, success or failure/timeout -- a listener
        # left registered past this call would keep tagging path_hits/
        # forbidden_hits for whatever the NEXT test's own walk does,
        # silently corrupting a result that has nothing to do with this
        # call at all.
        ctx.self_position.remove_listener(_on_position)

    return GotoWaypointResult(path_hits=path_hits, forbidden_hits=forbidden_hits)


async def assert_goto_never_arrives(
    ctx: TestContext, target_x: float, target_y: float, target_z: float, distance_tolerance: float, timeout: float,
) -> None:
    """Sends `!goto` toward a target the schematic marks as genuinely
    unreachable (magenta_wool -- see litematic.WAYPOINT_BLOCK_ROLES/
    Waypoints.unreachable's own docstrings, added per explicit direction:
    "the test should fail if it reaches this point") and asserts the bot
    NEVER comes within `distance_tolerance` of it during the full
    `timeout` window -- the inverse of goto()'s own pass condition. Raises
    `AssertionError` (not `asyncio.TimeoutError`, since here NOT timing
    out -- i.e. actually arriving -- is the failure) if the bot ever gets
    that close; returns normally (no exception) once `timeout` elapses
    with arrival never observed, meaning the bot correctly never found (or
    gave up looking for) a route to a target the scenario intends to be
    blocked.

    Uses a real 3D (x, y, z) distance, unlike goto()'s own arrival check
    (deliberately horizontal-only -- see its own docstring, matching real
    player movement having no independent vertical stop condition) --
    found live: an "unreachable" target sitting atop a blocking wall (see
    goto_impossible_1.litematic) can have the SAME (x, z) column as
    ground level at the wall's own base, so a horizontal-only check
    falsely reported "arrived" the instant the bot merely stood at the
    foot of the wall, nowhere near the target's real height. The whole
    point of "unreachable" is that the bot never gets physically close to
    that exact point, height included -- a target genuinely blocked by a
    wall the bot can't climb should never register as reached just
    because the bot is standing on the ground far below it.

    Deliberately does NOT reuse goto()'s own `asyncio.wait_for`-around-
    `_wait_for_arrival` shape directly -- goto() treats reaching `timeout`
    as the FAILURE (TimeoutError); this needs the opposite polarity (
    reaching `timeout` cleanly is the PASS), so it polls the same way but
    inverts what each outcome means.
    """
    await _wait_for_initial_position(ctx)
    await ctx.bridge.send_goto(target_x, target_y, target_z)

    async def _poll_for_unwanted_arrival() -> None:
        while True:
            pos = ctx.self_position.current
            if pos is not None:
                distance = math.dist((pos.x, pos.y, pos.z), (target_x, target_y, target_z))
                if distance <= distance_tolerance:
                    return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    try:
        await asyncio.wait_for(_poll_for_unwanted_arrival(), timeout=timeout)
    except asyncio.TimeoutError:
        return  # never arrived within the window -- exactly what a genuinely unreachable target should do
    raise AssertionError(
        f"bot reached supposedly unreachable target ({target_x}, {target_y}, {target_z}) "
        f"within {distance_tolerance} blocks -- expected it to never get there"
    )


async def reset_to_idle(ctx: TestContext) -> None:
    """Sends the same `{"type": "stop"}` wire command `!stop` sends --
    mod-side, MinebotMod.dispatchMessage's own "stop" case calls
    `playerIntention.stop()`, resetting PlayerIntentionState back to IDLE
    (see that Java enum's own docstring for the FOLLOW/DEFEND/IDLE axis
    this clears) -- so a leftover !follow/!defend from a previous manual
    session or an earlier test doesn't leak into the next test. Per
    explicit direction: test setup should start from a known, fully idle
    state, not just a known position -- a test that only teleports (see
    teleport() above) can still inherit an active FOLLOW/DEFEND goal from
    whatever ran before it, which would fight the test's own !goto/combat
    commands in ways that have nothing to do with what the test itself is
    supposed to be exercising.

    No wait/confirmation needed afterward (unlike teleport(), which polls
    `self_position` until the /tp visibly lands) -- there's no broadcast
    event confirming PlayerIntentionState changed the way `position`
    confirms a teleport; a caller that needs to confirm the reset actually
    landed should follow this with `assert_state(ctx, "player_intention",
    "IDLE", ...)`. Uses send_stop() (an unrated `_send`, like goto/follow/
    etc.), not send_chat -- this is a real command, not chat text.
    """
    await ctx.bridge.send_stop()


# Real network round trip (send query, mod replies) but no game-world
# action involved at all -- should resolve in well under a second in
# practice; kept short so a query that never gets a reply fails fast and
# visibly rather than silently stalling a test's own budget. This is the
# ONE-SHOT default for a single !query call -- callers polling with
# repeated queries in a loop (e.g. teleport() below) should use the much
# tighter POLL_QUERY_TIMEOUT_SECONDS instead, see its own docstring for
# the real budget-structure bug this distinction fixes.
QUERY_TIMEOUT_SECONDS = 5.0

# Per-attempt timeout for a query used INSIDE a polling loop, not a
# single one-shot call -- deliberately much shorter than
# QUERY_TIMEOUT_SECONDS. Found live: teleport()'s own polling loop passed
# QUERY_TIMEOUT_SECONDS (5.0) as EACH individual query_position() call's
# own timeout, while the loop itself was wrapped in an outer
# asyncio.wait_for using the SAME 5.0s figure (TELEPORT_TIMEOUT_SECONDS,
# a coincidentally equal but independently-set constant) -- if the first
# query attempt took anywhere close to its own full 5s allowance (real
# round-trip latency, nothing actually wrong), the OUTER wait_for could
# expire before that single query even finished, let alone before a
# second poll attempt ever got a chance to run. Confirmed live: the mod's
# own chat log showed a real "Teleported ritebot to ..." reply landing
# successfully, yet teleport() still raised TimeoutError roughly 4
# seconds later with no second query attempt logged at all. A real query
# round trip takes well under a second in practice (see
# QUERY_TIMEOUT_SECONDS's own docstring) -- this keeps a single slow/lost
# reply from silently eating most of the CALLER's own overall budget,
# leaving room for several real poll attempts within it instead of at
# most one.
POLL_QUERY_TIMEOUT_SECONDS = 1.0


async def query(ctx: TestContext, arg: str, timeout: float = QUERY_TIMEOUT_SECONDS) -> str:
    """Sends `{"type": "query", "arg": arg}` and returns the mod's own
    `result` string from its `query_result` reply (see minebot-mod's
    MinebotMod.handleQuery for the full list of supported `arg` values --
    "player_intention"/"legs"/"hands"/"head" as of this writing). Raises
    `RuntimeError` if the mod replies with an `"error"` instead (e.g. an
    unrecognized `arg`), and `asyncio.TimeoutError` (same shape as every
    other primitive here) if no reply arrives within `timeout` at all.

    Built specifically so tests can assert against the mod's own REAL
    final state machine state, not just position/timing-based proxies for
    it -- see assert_state's own docstring for the shared teardown
    assertion this is the plumbing for, and LegsStateMachine's own recent
    isStopCommand fix for the real bug (LegsState staying in GOTO after
    !stop) that went unnoticed for as long as it did specifically because
    nothing could assert against real final state before this existed.
    """
    await ctx.bridge.send_query(arg)
    event = await ctx.query_result.wait_for_next(timeout=timeout)
    if "error" in event.data:
        raise RuntimeError(f"query {arg!r} failed: {event.data['error']}")
    return event.data.get("result")


async def assert_state(ctx: TestContext, arg: str, expected: str, timeout: float = QUERY_TIMEOUT_SECONDS) -> None:
    """Queries `arg` (see query() above) and asserts the result equals
    `expected` -- the shared "did every state machine actually end up
    where it should" check meant to run from a test's own teardown/
    fixture, e.g. `assert_state(ctx, "legs", "IDLE")` after a !goto test
    to catch exactly the class of bug LegsStateMachine's own isStopCommand
    fix addressed (an axis silently left in a non-idle state that nothing
    was checking). A plain `assert query(...) == expected` inline would
    work just as well; this exists so every call site gets the same clear
    failure message shape instead of each writing its own.
    """
    actual = await query(ctx, arg, timeout=timeout)
    assert actual == expected, f"expected {arg}={expected!r}, got {actual!r}"


async def query_position(ctx: TestContext, timeout: float = QUERY_TIMEOUT_SECONDS) -> SelfPosition:
    """Sends `!query position` and returns the bot's real live position,
    read fresh on demand -- unlike `ctx.self_position.current` (only ever
    updated by broadcast `position` events, which the mod dedups
    exact-match: see MinebotMod.maybeBroadcastPositionEvent's own
    docstring), this always gets a genuine answer even if the bot has gone
    completely motionless and stopped broadcasting new position events
    entirely. Confirmed live as a real, previously-undiagnosable failure
    mode: a bot stuck fighting a genuinely unreachable !goto target
    (LegsGotoNode/PathTracker re-planning A* every tick with NO_PATH, no
    backoff) went fully motionless, position broadcasts stopped, and
    every subsequent goto()/teleport() call in the same session hung for
    its own full timeout waiting on a `position` event that was never
    coming again -- indistinguishable from a dead connection from
    Python's own side, even though the client was alive and ticking
    normally the whole time. Does NOT update ctx.self_position itself
    (that tracker is fed only by real broadcast events, by design -- see
    its own docstring) -- this is a one-shot read for diagnosis/assertion,
    not a way to force a stale tracker fresh.
    """
    await ctx.bridge.send_query("position")
    event = await ctx.query_result.wait_for_next(timeout=timeout)
    if "error" in event.data:
        raise RuntimeError(f"query 'position' failed: {event.data['error']}")
    data = event.data["position"]
    return SelfPosition(x=data["x"], y=data["y"], z=data["z"], yaw=data["yaw"], pitch=data["pitch"])


async def query_block(ctx: TestContext, x: int, y: int, z: int, timeout: float = QUERY_TIMEOUT_SECONDS) -> str:
    """Sends `!query block <x> <y> <z>` and returns the real block ID at
    that world position (e.g. "minecraft:air", "minecraft:stone"), read
    straight off the mod's own loaded chunk data -- the same data
    pathfinding itself reads (see Movements' own getNeighbors/
    safeOrBreak). Added to debug a real live report: NO_PATH reported on
    ground a human operator confirmed was flat/void -- this lets a test
    (or a human via !query block) directly confirm what block the mod
    itself actually sees at an exact coordinate, ruling out (or
    confirming) a real discrepancy between the confirmed-flat terrain and
    what pathfinding's own reads see. Raises `RuntimeError` if the mod
    replies with an `"error"` (e.g. the chunk genuinely isn't loaded).
    """
    await ctx.bridge.send_query("block", x=x, y=y, z=z)
    event = await ctx.query_result.wait_for_next(timeout=timeout)
    if "error" in event.data:
        raise RuntimeError(f"query 'block' at ({x}, {y}, {z}) failed: {event.data['error']}")
    return event.data["block"]


# `/tp` itself is exact (no pathfinding, no arrival tolerance the way
# !goto has) -- this only needs to be loose enough to absorb float
# formatting/rounding in the command string and one tick of position-
# broadcast lag, not real movement imprecision.
TELEPORT_TOLERANCE = 0.1


async def teleport(ctx: TestContext, x: float, y: float, z: float, timeout: float) -> None:
    """Teleports the bot to a fixed position via a structured `teleport`
    wire command (see ModBridge.send_teleport's own docstring, and
    MinebotMod's own "teleport" dispatch case -- sends the same real
    vanilla `/tp @s x y z` a human typing in chat uses, but ALSO zeros the
    player's own residual velocity/fall distance right after, unlike a
    plain `/tp` sent as raw chat text), then confirms arrival via `!query
    position` (see query_position's own docstring) before returning.
    Exists so tests can start from a known, fixed origin
    instead of "wherever the bot happened to be left standing by the
    previous test" -- per explicit direction, this is what a test's own
    setup() should call (see TestCase.setup/runner.py) so every test
    starts from the same place regardless of run order or what an earlier
    test in the same session did.

    Deliberately polls via ACTIVE `!query position` requests, not the
    passive broadcast `position` event `ctx.self_position.current` only
    ever reflects -- per explicit direction ("use query position not as a
    fallback but as the main way for teleport"), after a real live bug:
    the broadcast is exact-dedup'd (see MinebotMod.
    maybeBroadcastPositionEvent's own docstring), and a teleport TO a
    position the bot happens to already be standing at/near (e.g. every
    test's own teardown teleporting to the same fixed HOLDING spot in a
    row) can leave nothing NEW to broadcast even though the /tp itself
    genuinely executed -- confirmed live via the mod's own chat log
    showing a real "Teleported ritebot to ..." reply while Python's own
    passive-broadcast-based poll kept waiting, timed out, and retried the
    exact same /tp a second time moments later. Querying fresh each poll
    sidesteps the dedup entirely: it always gets a genuine, current answer
    regardless of whether anything actually changed since the last one.

    Raises `asyncio.TimeoutError` (same shape as goto()/wait_for_position()
    above) if the position never actually converges within `timeout` --
    e.g. a malformed command, or the bot not actually connected.

    Uses send_teleport (an unrated `_send`, same as send_console_command),
    not send_chat -- found live: a test-world `/tp` sitting behind other
    queued chat (CHAT_RATE_PER_SECOND's own 1/sec throttle, meant for real
    multiplayer spam risk that doesn't apply to the disposable/
    single-player test world at all -- see send_console_command's own
    docstring, already applied to place_schematic/clear_schematic) could
    eat several real seconds of `timeout`'s own budget before the /tp
    even reached the server, causing sporadic teleport timeouts that had
    nothing to do with the teleport itself ever actually failing.
    """
    async def _wait_for_teleport() -> None:
        await ctx.bridge.send_teleport(x, y, z)

        while True:
            # POLL_QUERY_TIMEOUT_SECONDS, not QUERY_TIMEOUT_SECONDS -- see
            # its own docstring for the real budget-structure bug this
            # fixes: a single slow/lost query reply must not be able to
            # eat this whole call's own `timeout` budget on its own. A
            # single missed/slow reply here is retried (see except below),
            # not treated as a hard failure -- only running out of the
            # OUTER `timeout` (this whole function is wrapped in
            # asyncio.wait_for below) actually fails the teleport.
            try:
                pos = await query_position(ctx, timeout=POLL_QUERY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                continue
            distance = math.dist((pos.x, pos.y, pos.z), (x, y, z))
            if distance <= TELEPORT_TOLERANCE:
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    await asyncio.wait_for(_wait_for_teleport(), timeout=timeout)


# Grace period after the LAST `/fill` command is sent, for its own real
# round trip through the single-player integrated server to land -- there's
# no mod-broadcast event confirming a `/fill` landed the way `position`
# confirms a teleport (see teleport()'s own `/tp` handling above), so this
# is a fixed pad rather than a real poll-until-confirmed wait. Commands
# themselves go out back-to-back with no artificial spacing between them
# (see send_console_command's own docstring for why CHAT_RATE_PER_SECOND
# doesn't apply to the disposable test world at all -- it's single-player,
# nothing else to spam).
_FILL_LAND_GRACE_SECONDS = 1.0


def _fill_runs(schematic: Schematic) -> list[tuple[int, int, int, int, int, int, str]]:
    """Collapses a schematic's individual blocks into axis-aligned same-
    block-type runs along x (within one (y, z) row), each replayable as one
    `/fill` command instead of one `/setblock` per block -- keeps the real
    command count (and therefore real round trips to the client) down for
    a solid floor/wall, even though send_console_command has no rate limit
    to work around here (see its own docstring -- that's specific to real
    multiplayer chat, not the single-player test world). Only merges along x
    (not a full 3D greedy merge) -- schematics built for THIS repo's own
    test scenarios are expected to be small/simple (a handful of blocks
    marking a goal position, a short wall), where row-merging already
    collapses the common case (a flat floor/wall) to one run per row; a
    full 3D box-merge isn't worth the complexity until a real scenario
    actually needs it.

    Returns (x1, y, z, x2, y, z, block) tuples, one per contiguous run,
    in ascending (y, z, x) order -- deterministic, so tests calling this
    directly get reproducible output.
    """
    by_row: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for b in schematic.blocks:
        by_row.setdefault((b.y, b.z), []).append((b.x, b.block))

    runs = []
    for (y, z), cells in sorted(by_row.items()):
        cells.sort()
        run_start_x, run_block = cells[0]
        prev_x = run_start_x
        for x, block in cells[1:]:
            if block == run_block and x == prev_x + 1:
                prev_x = x
                continue
            runs.append((run_start_x, y, z, prev_x, y, z, run_block))
            run_start_x, run_block = x, block
            prev_x = x
        runs.append((run_start_x, y, z, prev_x, y, z, run_block))
    return runs


async def place_schematic(ctx: TestContext, schematic: Schematic, anchor_x: int, anchor_y: int, anchor_z: int, timeout: float) -> None:
    """Places `schematic` (see litematic.py -- read from a `.litematic`
    file built in-game with Litematica, or constructed directly) into the
    world via real `/fill` commands, offset so its own (0,0,0) corner lands
    at (anchor_x, anchor_y, anchor_z). Per explicit direction, a test's own
    setup should build its scenario this way rather than relying on
    whatever the disposable test world happened to already contain, then
    call clear_schematic (same anchor) in teardown to restore the world to
    empty for the next test -- see clear_schematic's own docstring for why
    that's a plain `/fill air` over the bounding box, not a snapshot/
    restore of pre-existing blocks (the disposable test world always
    starts as an empty void, see minebot-mod's TESTING.md -- there is
    nothing to preserve underneath a freshly-placed test scenario).

    Runs are collapsed via _fill_runs to keep the real command count
    proportional to the schematic's own distinct same-block-type ROWS, not
    its total block count -- sent back-to-back via send_console_command
    (not send_chat -- see that method's own docstring for why the normal
    chat rate limit doesn't apply to the disposable, single-player test
    world at all).
    """
    runs = _fill_runs(schematic)

    async def _place() -> None:
        for x1, y1, z1, x2, y2, z2, block in runs:
            command = (
                f"/fill {anchor_x + x1} {anchor_y + y1} {anchor_z + z1} "
                f"{anchor_x + x2} {anchor_y + y2} {anchor_z + z2} {block}"
            )
            await ctx.bridge.send_console_command(command)

        # There's no mod-broadcast event confirming a /fill landed (see
        # _FILL_LAND_GRACE_SECONDS's own docstring) -- a fixed pad after
        # the last command before returning, so a caller that immediately
        # starts asserting against placed blocks doesn't race the world's
        # own real, if fast, round trip.
        await asyncio.sleep(_FILL_LAND_GRACE_SECONDS)

    await asyncio.wait_for(_place(), timeout=timeout)


async def clear_schematic(ctx: TestContext, schematic: Schematic, anchor_x: int, anchor_y: int, anchor_z: int, timeout: float) -> None:
    """Restores the region a matching place_schematic call occupied back to
    air -- ONE `/fill ... air` over the schematic's own full bounding box
    (anchor to anchor+size-1 on every axis), not a block-by-block undo or a
    snapshot-restore of whatever was there before. Correct specifically
    because the disposable test world always starts as an empty void (see
    place_schematic's own docstring) -- there is no pre-existing state to
    put back, only the test's own placed blocks to remove, so "fill with
    air" and "restore the previous state" are the same operation here.

    Deliberately takes the same (schematic, anchor_x, anchor_y, anchor_z)
    shape place_schematic does rather than a raw bounding box, so a test's
    teardown can't accidentally clear the wrong region by hand-computing
    bounds that drift out of sync with what setup actually placed. Bounded
    by `timeout` the same way place_schematic is -- a stuck teardown should
    fail fast and visibly, not hang the whole test run (see run_test_case's
    own docstring for why every test-adjacent await needs its own bound).
    """
    x2 = anchor_x + schematic.size_x - 1
    y2 = anchor_y + schematic.size_y - 1
    z2 = anchor_z + schematic.size_z - 1
    command = f"/fill {anchor_x} {anchor_y} {anchor_z} {x2} {y2} {z2} air replace"

    async def _clear() -> None:
        await ctx.bridge.send_console_command(command)
        await asyncio.sleep(_FILL_LAND_GRACE_SECONDS)

    await asyncio.wait_for(_clear(), timeout=timeout)
