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

from minebot.bridge.self_position import SelfPosition
from minebot.testing.litematic import Schematic
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
    event that would confirm PlayerIntentionState changed, and none of
    this repo's own tests currently assert against it directly; they rely
    on it only insofar as leftover FOLLOW/DEFEND would otherwise interfere
    with movement/combat commands the test DOES assert on. Uses
    send_stop() (an unrated `_send`, like goto/follow/etc.), not
    send_chat -- this is a real command, not chat text.
    """
    await ctx.bridge.send_stop()


# `/tp` itself is exact (no pathfinding, no arrival tolerance the way
# !goto has) -- this only needs to be loose enough to absorb float
# formatting/rounding in the command string and one tick of position-
# broadcast lag, not real movement imprecision.
TELEPORT_TOLERANCE = 0.1


async def teleport(ctx: TestContext, x: float, y: float, z: float, timeout: float) -> None:
    """Teleports the bot to a fixed position via a real `/tp @s x y z`
    chat/console command (see MinebotMod's own "chat" dispatch case --
    the same real vanilla command-send path a human typing in chat uses,
    not a separate debug-only teleport), then waits for `self_position`
    to actually reflect the new position before returning. Exists so
    tests can start from a known, fixed origin instead of "wherever the
    bot happened to be left standing by the previous test" -- per
    explicit direction, this is what a test's own setup() should call
    (see TestCase.setup/runner.py) so every test starts from the same
    place regardless of run order or what an earlier test in the same
    session did.

    Waits for the position to actually change (not just sends and
    returns) because `bridge.send_chat` only ENQUEUES the command (see
    its own docstring -- rate-limited, doesn't wait for its actual turn
    through the queue) -- a caller that immediately reads self_position.
    current after calling this without waiting could still see the
    bot's PREVIOUS position, race the real teleport, and start a test
    from the wrong place with no error to explain why. Raises
    `asyncio.TimeoutError` (same shape as goto()/wait_for_position()
    above) if the position never actually updates within `timeout` --
    e.g. a malformed command, or the bot not actually connected.
    """
    await _wait_for_initial_position(ctx)

    async def _wait_for_teleport() -> None:
        await ctx.bridge.send_chat(f"/tp @s {x} {y} {z}")

        while True:
            pos = ctx.self_position.current
            if pos is not None:
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
