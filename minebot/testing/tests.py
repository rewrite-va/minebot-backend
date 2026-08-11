"""The actual registered in-game tests -- see runner.py's own docstring
for the overall !runtest design. Each test is a plain async function that
sends a goal and asserts an outcome via the shared, self-timing-out
primitives in actions.py (see its own docstring for why bare unbounded
poll loops here are unsafe once a test is called from somewhere -- like
the pytest integration driver -- that doesn't already wrap it in a
timeout).

Every test's own `setup` teleports to a FIXED origin first (see
ORIGIN/actions.teleport) rather than computing targets relative to
wherever the bot happens to already be standing -- per explicit
direction: a target computed relative to the bot's live (possibly
leftover-from-a-previous-test, or a manual play session's) position made
tests order-dependent and non-reproducible. Every test in this file can
now assume it starts from the exact same known spot, regardless of what
ran before it or how the world was last left.
"""

from __future__ import annotations

from pathlib import Path

from minebot.testing import actions
from minebot.testing.litematic import Schematic, Waypoint
from minebot.testing.runner import TestCase, TestContext, TestRegistry

# A fixed point on the disposable test world's own flat/void floor (see
# minebot-mod's TESTING.md "The disposable test world" -- superflat, the
# void preset) -- every test's own setup teleports here first. y=-60 is
# not an arbitrary guess: it's the real spawn height this exact world
# generation has produced consistently across every launch observed so
# far (confirmed live via the mod's own broadcast `position` events).
ORIGIN_X = 0.0
ORIGIN_Y = -60.0
ORIGIN_Z = 0.0
TELEPORT_TIMEOUT_SECONDS = 10.0

# Matches minebot-mod's own LegsGotoNode.ARRIVAL_DISTANCE -- !goto's own
# real arrival precision, confirmed live via the mod's own broadcast
# `position` events (the bot's reported (x, z) actually converges to
# within this of the target, not just "eventually stops somewhere
# nearby"). Was NavIntent.defaultStopDistance() (2.0, Follow's own much
# looser "don't crowd a moving target" tolerance) until a real report:
# a test tightened to 0.5 blocks hung forever because the mod's OWN
# arrival check at the time was still the loose 2.0-block one, never
# actually reaching within 0.5 of the target at all -- fixed mod-side by
# giving LegsGotoNode its own dedicated, tighter ARRIVAL_DISTANCE (see
# its own docstring) rather than loosening this test to match a
# coincidentally-reused default that was never meant for "go to this
# exact spot" in the first place.
GOTO_ARRIVAL_TOLERANCE = 0.5
GOTO_TIMEOUT_SECONDS = 15.0
GOTO_TARGET_X = ORIGIN_X
GOTO_TARGET_Y = ORIGIN_Y
GOTO_TARGET_Z = ORIGIN_Z + 5.0


async def setup_goto(ctx: TestContext) -> None:
    """Teleports the bot to the fixed ORIGIN via a real `/tp` command --
    see this module's own docstring for why every test starts from a
    known position rather than wherever it happened to already be. Also
    resets PlayerIntention to IDLE (see actions.reset_to_idle's own
    docstring) so a leftover !follow/!defend from a manual session or an
    earlier test can't fight this test's own !goto command.
    """
    await actions.reset_to_idle(ctx)
    await actions.teleport(ctx, ORIGIN_X, ORIGIN_Y, ORIGIN_Z, timeout=TELEPORT_TIMEOUT_SECONDS)


async def test_goto_moves_bot_to_target(ctx: TestContext) -> None:
    """The first-slice test from minebot-mod's TESTING.md: send a fixed
    !goto target a real distance from ORIGIN, then assert the bot
    actually arrives within GOTO_ARRIVAL_TOLERANCE. Assumes setup_goto
    already ran (via TestCase.setup/run_test_case -- see runner.py) and
    the bot is standing at ORIGIN, not wherever it happened to be left
    before this test started.
    """
    await actions.goto(
        ctx,
        target_x=GOTO_TARGET_X,
        target_y=GOTO_TARGET_Y,
        target_z=GOTO_TARGET_Z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=GOTO_TIMEOUT_SECONDS,
    )


# A schematic built in-game with Litematica (place blocks by hand in
# creative, `/litematic create`), then exported as-is -- see litematic.py's
# own docstring for why this is read directly (a small hand-rolled NBT
# reader) rather than requiring Litematica itself as a mod dependency of
# the disposable test client. Checked into the repo as a fixture (not read
# from the user's own live Prism instance) so this test is reproducible on
# any checkout, not just this machine's current schematics folder.
SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "simple_goto.litematic"

# Anchored at ORIGIN's own (x, z) -- per explicit direction: every
# schematic-driven test's own teardown (clear_schematic) always fully
# clears its footprint before the NEXT test's setup ever places anything
# (run_test_case's own sequencing, plus TestRunner running tests strictly
# one at a time -- see its own docstring), so spreading each schematic out
# to a different, far-off X offset bought nothing beyond keeping tests
# out of each other's way, and cost real robustness on a non-void/
# non-superflat world (e.g. a manual !runtest session against a real LAN
# world, not just the disposable pytest-launched one): those far
# coordinates can land in unloaded chunks or real terrain that doesn't
# actually match a flat void floor, which is exactly the kind of thing
# that caused a real live "No blocks were filled" + fell-out-of-world
# failure. Every schematic-driven test now shares ORIGIN's own (x, z),
# only Y offsets from the schematic's own local coordinates -- same
# floor, same chunk, always confirmed loaded/reachable since ORIGIN
# itself is where every test's OWN first teleport already lands safely.
SCHEMATIC_ANCHOR_X = ORIGIN_X
SCHEMATIC_ANCHOR_Y = ORIGIN_Y
SCHEMATIC_ANCHOR_Z = ORIGIN_Z

SCHEMATIC_TIMEOUT_SECONDS = 15.0


def _one(waypoints: list, role: str) -> object:
    """Validates a schematic's own wool-marker convention down to exactly
    one waypoint for roles that only make sense singular (start/end) --
    see litematic.Waypoints' own docstring for why extraction itself
    stays permissive (a plain list, any count) while call sites that
    genuinely need exactly one enforce that themselves: a schematic with
    two white_wool blocks is a real authoring mistake in Litematica, and
    should fail here with a clear message, not silently pick whichever one
    the block-state array happened to read first.
    """
    if len(waypoints) != 1:
        raise ValueError(f"expected exactly one {role!r} waypoint, found {len(waypoints)}")
    return waypoints[0]


def _offset(anchor_x: float, anchor_y: float, anchor_z: float, waypoint: Waypoint) -> Waypoint:
    return Waypoint(x=int(anchor_x) + waypoint.x, y=int(anchor_y) + waypoint.y, z=int(anchor_z) + waypoint.z)


def _make_schematic_setup(schematic_path: Path, anchor_x: float, anchor_y: float, anchor_z: float):
    """Builds a TestCase.setup for a schematic-driven scenario: reset to
    IDLE, teleport to the safe, always-solid ORIGIN, place the schematic
    at the given anchor, THEN teleport to the schematic's own white-wool
    "start" marker (see litematic.WAYPOINT_BLOCK_ROLES) -- the shared
    shape every wool-marker scenario test in this file uses (see
    setup_goto_onto_schematic's own original docstring, now generalized
    here once a second/third schematic-driven test -- goto_jump/
    goto_impossible -- made the copy-pasted version worth factoring out).

    Teleporting STRAIGHT to `start` before anything is placed was the
    original (buggy) order -- confirmed live: the disposable test world's
    own void floor sits at a fixed ORIGIN_Y, so a `start` marker anchored
    somewhere above where the schematic's OWN floor blocks will eventually
    go has nothing solid under it yet at teleport time. The bot fell
    straight through into the void, landed on the far-below void floor
    instead of `start`'s own y, and actions.teleport's own arrival check
    (a real 3D distance, not just x/z) then polled for the FULL
    TELEPORT_TIMEOUT_SECONDS waiting for a y that was never going to
    happen -- raising asyncio.TimeoutError before place_schematic ever
    even ran, which is why NO /fill commands went out at all and only the
    exception-path teardown's own single "clear" fill showed up in the
    wire log. Landing at ORIGIN first (the void floor itself -- always
    solid, real ground under real feet) sidesteps that entirely: by the
    time this teleports to `start`, the schematic's own blocks already
    exist to land on.
    """
    async def setup(ctx: TestContext) -> None:
        await actions.reset_to_idle(ctx)
        await actions.teleport(ctx, ORIGIN_X, ORIGIN_Y, ORIGIN_Z, timeout=TELEPORT_TIMEOUT_SECONDS)
        schematic = Schematic.from_file(schematic_path)
        await actions.place_schematic(
            ctx, schematic, anchor_x=int(anchor_x), anchor_y=int(anchor_y), anchor_z=int(anchor_z),
            timeout=SCHEMATIC_TIMEOUT_SECONDS,
        )
        start = _one(schematic.waypoints.start, "start")
        await actions.teleport(
            ctx, anchor_x + start.x, anchor_y + start.y, anchor_z + start.z,
            timeout=TELEPORT_TIMEOUT_SECONDS,
        )

    return setup


def _make_schematic_teardown(schematic_path: Path, anchor_x: float, anchor_y: float, anchor_z: float):
    """Builds a TestCase.teardown clearing whatever _make_schematic_setup's
    own place_schematic call placed -- see clear_schematic's own docstring
    for why this is correct without a snapshot/restore (the disposable
    test world always starts as an empty void). Runs regardless of
    whether the test itself passed or failed (TestCase.teardown/
    run_test_case's own docstrings) so a failed test never leaves blocks
    behind for the next one.
    """
    async def teardown(ctx: TestContext) -> None:
        schematic = Schematic.from_file(schematic_path)
        await actions.clear_schematic(
            ctx, schematic, anchor_x=int(anchor_x), anchor_y=int(anchor_y), anchor_z=int(anchor_z),
            timeout=SCHEMATIC_TIMEOUT_SECONDS,
        )

    return teardown


setup_goto_onto_schematic = _make_schematic_setup(SCHEMATIC_PATH, SCHEMATIC_ANCHOR_X, SCHEMATIC_ANCHOR_Y, SCHEMATIC_ANCHOR_Z)
teardown_clear_schematic = _make_schematic_teardown(SCHEMATIC_PATH, SCHEMATIC_ANCHOR_X, SCHEMATIC_ANCHOR_Y, SCHEMATIC_ANCHOR_Z)


async def test_goto_arrives_on_schematic_block(ctx: TestContext) -> None:
    """Sends !goto to the schematic's own green/lime-wool "end" marker
    (rather than bare empty-void coordinates, the only kind of target
    every other test here uses) and asserts arrival, plus that the walk
    actually passed through every yellow "path" waypoint and never
    touched a red "forbidden" one (see actions.goto_with_waypoints) --
    proves the place-schematic/clear-schematic round trip actually leaves
    real, standable blocks in the world (not just that the /fill commands
    were sent without error) AND that the bot's real walked trail matches
    the scenario as authored visually in Litematica, not just that it
    eventually arrived somewhere close to the end marker.
    """
    schematic = Schematic.from_file(SCHEMATIC_PATH)
    end = _one(schematic.waypoints.end, "end")

    result = await actions.goto_with_waypoints(
        ctx,
        target_x=SCHEMATIC_ANCHOR_X + end.x,
        target_y=SCHEMATIC_ANCHOR_Y + end.y,
        target_z=SCHEMATIC_ANCHOR_Z + end.z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=GOTO_TIMEOUT_SECONDS,
        path=[_offset(SCHEMATIC_ANCHOR_X, SCHEMATIC_ANCHOR_Y, SCHEMATIC_ANCHOR_Z, w) for w in schematic.waypoints.path],
        forbidden=[_offset(SCHEMATIC_ANCHOR_X, SCHEMATIC_ANCHOR_Y, SCHEMATIC_ANCHOR_Z, w) for w in schematic.waypoints.forbidden],
    )

    for i, hits in enumerate(result.path_hits):
        assert hits > 0, f"never walked through path waypoint {schematic.waypoints.path[i]}"
    for i, hits in enumerate(result.forbidden_hits):
        assert hits == 0, f"walked through forbidden waypoint {schematic.waypoints.forbidden[i]}"


# A jump-across-a-gap scenario: start on one side, path marker above the
# gap (must be walked/jumped over), forbidden marker in the gap itself at
# foot height (falling in is a failure), end on the far side.
JUMP_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_jump_1.litematic"
# Same ORIGIN (x, z) every schematic-driven test now shares -- see
# SCHEMATIC_ANCHOR_X's own comment for why spreading these out to
# different X offsets was dropped.
JUMP_SCHEMATIC_ANCHOR_X = ORIGIN_X
JUMP_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
JUMP_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_jump = _make_schematic_setup(JUMP_SCHEMATIC_PATH, JUMP_SCHEMATIC_ANCHOR_X, JUMP_SCHEMATIC_ANCHOR_Y, JUMP_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_jump = _make_schematic_teardown(JUMP_SCHEMATIC_PATH, JUMP_SCHEMATIC_ANCHOR_X, JUMP_SCHEMATIC_ANCHOR_Y, JUMP_SCHEMATIC_ANCHOR_Z)


async def test_goto_jumps_across_gap(ctx: TestContext) -> None:
    """Sends !goto to goto_jump_1's own "end" marker across a one-block
    gap and asserts the bot's real walked trail passed over the "path"
    waypoint (above the gap) and never entered the "forbidden" one (the
    gap itself, at foot height -- falling in would be a real pathfinding
    regression, not just a slower route). Exercises real jump/pathfinding
    logic (LegsNavigateNode's own A* port), unlike test_goto_moves_bot_to_target's
    flat, obstacle-free walk.
    """
    schematic = Schematic.from_file(JUMP_SCHEMATIC_PATH)
    end = _one(schematic.waypoints.end, "end")
    anchor = (JUMP_SCHEMATIC_ANCHOR_X, JUMP_SCHEMATIC_ANCHOR_Y, JUMP_SCHEMATIC_ANCHOR_Z)

    result = await actions.goto_with_waypoints(
        ctx,
        target_x=JUMP_SCHEMATIC_ANCHOR_X + end.x,
        target_y=JUMP_SCHEMATIC_ANCHOR_Y + end.y,
        target_z=JUMP_SCHEMATIC_ANCHOR_Z + end.z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=GOTO_TIMEOUT_SECONDS,
        path=[_offset(*anchor, w) for w in schematic.waypoints.path],
        forbidden=[_offset(*anchor, w) for w in schematic.waypoints.forbidden],
    )

    for i, hits in enumerate(result.path_hits):
        assert hits > 0, f"never walked through path waypoint {schematic.waypoints.path[i]}"
    for i, hits in enumerate(result.forbidden_hits):
        assert hits == 0, f"fell into forbidden waypoint {schematic.waypoints.forbidden[i]}"


# A genuinely blocked scenario: start on one side of a 2-tall, 1-wide
# stone wall with no way around it in the schematic's own footprint, and a
# magenta_wool "unreachable" marker on the far side -- see
# litematic.Waypoints.unreachable's own docstring for why this is its own
# role, not `forbidden` (this IS the !goto target, not just a column to
# avoid on the way to a different real goal).
IMPOSSIBLE_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_impossible_1.litematic"
# Same ORIGIN (x, z) every schematic-driven test now shares -- see
# SCHEMATIC_ANCHOR_X's own comment for why spreading these out to
# different X offsets was dropped.
IMPOSSIBLE_SCHEMATIC_ANCHOR_X = ORIGIN_X
IMPOSSIBLE_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
IMPOSSIBLE_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

# Genuinely blocked, so !goto never converges on its own the way a normal
# test's own arrival does -- this only needs to be long enough to be
# confident the mod actually gave up / kept failing to find a route, not
# so long every full pytest run pays for it needlessly. Shorter than
# GOTO_TIMEOUT_SECONDS on purpose: there's no arrival to wait out here,
# just a fixed observation window.
IMPOSSIBLE_GOTO_WINDOW_SECONDS = 10.0

setup_goto_impossible = _make_schematic_setup(IMPOSSIBLE_SCHEMATIC_PATH, IMPOSSIBLE_SCHEMATIC_ANCHOR_X, IMPOSSIBLE_SCHEMATIC_ANCHOR_Y, IMPOSSIBLE_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_impossible = _make_schematic_teardown(IMPOSSIBLE_SCHEMATIC_PATH, IMPOSSIBLE_SCHEMATIC_ANCHOR_X, IMPOSSIBLE_SCHEMATIC_ANCHOR_Y, IMPOSSIBLE_SCHEMATIC_ANCHOR_Z)


async def test_goto_never_reaches_unreachable_target(ctx: TestContext) -> None:
    """Sends !goto toward goto_impossible_1's own magenta "unreachable"
    marker and asserts the bot never actually gets there (see
    actions.assert_goto_never_arrives) -- per explicit direction: "the
    test should fail if it reaches this point". Proves the mod doesn't
    somehow clip/dig/glitch through a real blocking wall with no walkable
    route around it in the schematic's own footprint.
    """
    schematic = Schematic.from_file(IMPOSSIBLE_SCHEMATIC_PATH)
    unreachable = _one(schematic.waypoints.unreachable, "unreachable")

    await actions.assert_goto_never_arrives(
        ctx,
        target_x=IMPOSSIBLE_SCHEMATIC_ANCHOR_X + unreachable.x,
        target_y=IMPOSSIBLE_SCHEMATIC_ANCHOR_Y + unreachable.y,
        target_z=IMPOSSIBLE_SCHEMATIC_ANCHOR_Z + unreachable.z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    )


def register_default_tests(registry: TestRegistry) -> None:
    registry.register(TestCase(
        name="goto",
        description="Sends the bot 5 blocks away via !goto and asserts it actually arrives.",
        func=test_goto_moves_bot_to_target,
        setup=setup_goto,
    ))
    registry.register(TestCase(
        name="goto_onto_schematic",
        description="Places a Litematica-built fixture schematic, sends !goto onto it, and asserts arrival -- then always clears the placed blocks again.",
        func=test_goto_arrives_on_schematic_block,
        setup=setup_goto_onto_schematic,
        teardown=teardown_clear_schematic,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_jump",
        description="Places a schematic with a one-block gap, sends !goto across it, and asserts the bot jumped over the gap without falling in.",
        func=test_goto_jumps_across_gap,
        setup=setup_goto_jump,
        teardown=teardown_clear_goto_jump,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_impossible",
        description="Places a schematic with a genuinely blocked target, sends !goto toward it, and asserts the bot never actually reaches it.",
        func=test_goto_never_reaches_unreachable_target,
        setup=setup_goto_impossible,
        teardown=teardown_clear_goto_impossible,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    ))
