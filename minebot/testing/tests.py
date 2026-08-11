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

# Placed a real distance from ORIGIN (not overlapping it) so setup's own
# placement and the pre-existing origin/goto-target scenario above can
# never collide regardless of test order.
SCHEMATIC_ANCHOR_X = ORIGIN_X + 20.0
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


async def setup_goto_onto_schematic(ctx: TestContext) -> None:
    """Resets PlayerIntention to IDLE, teleports to the schematic's own
    white-wool "start" marker (see litematic.WAYPOINT_BLOCK_ROLES), then
    places the fixture schematic at SCHEMATIC_ANCHOR -- all inside this
    TestCase's own combined timeout budget (see run_test_case's own
    docstring). Teleporting to the marker itself (not a hardcoded
    ORIGIN-relative offset) is the whole point of the wool-marker
    convention: the test's own starting position is authored visually in
    Litematica, not duplicated as separate coordinates here that could
    silently drift out of sync with what the schematic actually shows.
    """
    await actions.reset_to_idle(ctx)
    schematic = Schematic.from_file(SCHEMATIC_PATH)
    start = _one(schematic.waypoints.start, "start")
    await actions.teleport(
        ctx,
        SCHEMATIC_ANCHOR_X + start.x, SCHEMATIC_ANCHOR_Y + start.y, SCHEMATIC_ANCHOR_Z + start.z,
        timeout=TELEPORT_TIMEOUT_SECONDS,
    )
    await actions.place_schematic(
        ctx, schematic,
        anchor_x=int(SCHEMATIC_ANCHOR_X), anchor_y=int(SCHEMATIC_ANCHOR_Y), anchor_z=int(SCHEMATIC_ANCHOR_Z),
        timeout=SCHEMATIC_TIMEOUT_SECONDS,
    )


async def teardown_clear_schematic(ctx: TestContext) -> None:
    """Restores the region setup_goto_onto_schematic placed back to air --
    see clear_schematic's own docstring for why this is correct without a
    snapshot/restore (the disposable test world always starts as an empty
    void, so there's nothing under the placed blocks to preserve). Runs
    regardless of whether the test itself passed or failed (see
    TestCase.teardown/run_test_case's own docstrings) so a failed test
    never leaves blocks behind for the next test to trip over.
    """
    schematic = Schematic.from_file(SCHEMATIC_PATH)
    await actions.clear_schematic(
        ctx, schematic,
        anchor_x=int(SCHEMATIC_ANCHOR_X), anchor_y=int(SCHEMATIC_ANCHOR_Y), anchor_z=int(SCHEMATIC_ANCHOR_Z),
        timeout=SCHEMATIC_TIMEOUT_SECONDS,
    )


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

    def _offset(waypoint):
        return Waypoint(
            x=int(SCHEMATIC_ANCHOR_X) + waypoint.x,
            y=int(SCHEMATIC_ANCHOR_Y) + waypoint.y,
            z=int(SCHEMATIC_ANCHOR_Z) + waypoint.z,
        )

    result = await actions.goto_with_waypoints(
        ctx,
        target_x=SCHEMATIC_ANCHOR_X + end.x,
        target_y=SCHEMATIC_ANCHOR_Y + end.y,
        target_z=SCHEMATIC_ANCHOR_Z + end.z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=GOTO_TIMEOUT_SECONDS,
        path=[_offset(w) for w in schematic.waypoints.path],
        forbidden=[_offset(w) for w in schematic.waypoints.forbidden],
    )

    for i, hits in enumerate(result.path_hits):
        assert hits > 0, f"never walked through path waypoint {schematic.waypoints.path[i]}"
    for i, hits in enumerate(result.forbidden_hits):
        assert hits == 0, f"walked through forbidden waypoint {schematic.waypoints.forbidden[i]}"


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
        timeout_seconds=SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
