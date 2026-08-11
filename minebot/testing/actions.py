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
