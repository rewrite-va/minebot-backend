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


def _block_center(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Offsets a block's own integer (x, y, z) coordinate to its real
    HORIZONTAL center for `/tp` -- a block occupies x in [x, x+1) and z in
    [z, z+1) (Minecraft's own convention: the integer coordinate is the
    block's minimum corner, not its center), so `/tp @s x y z` with a bare
    integer x/z lands the bot's feet at that corner/EDGE of the block, not
    its middle. Found live, per direct report: every teleport target in
    this file (ORIGIN, HOLDING, GOTO_TARGET, a schematic's own "start"
    waypoint, ...) was a plain integer x/z, so every teleport was landing
    at a block's edge rather than its center. Y is deliberately left
    UNCHANGED -- standing "on top of" a block at its own real Y (the
    block's top surface) is already correct with no offset needed;
    only X/Z (horizontal position within the block's own footprint) were
    ever wrong.
    """
    return (x + 0.5, y, z + 0.5)

# A fixed point on the disposable test world's own flat/void floor (see
# minebot-mod's TESTING.md "The disposable test world" -- superflat, the
# void preset) -- every test's own setup teleports here first. y=-60 is
# not an arbitrary guess: it's the real spawn height this exact world
# generation has produced consistently across every launch observed so
# far (confirmed live via the mod's own broadcast `position` events).
ORIGIN_X = 0.0
ORIGIN_Y = -60.0
ORIGIN_Z = 0.0
# Kept short (5s) per explicit direction -- a teleport/goto/schematic
# placement/blocked-target observation that's actually working correctly
# should converge well under this; anything genuinely stuck should fail
# fast and visibly rather than burning a much longer budget first.
TELEPORT_TIMEOUT_SECONDS = 5.0

# A holding spot well outside every schematic's own footprint (all
# anchored at ORIGIN's own (x, z) -- see SCHEMATIC_ANCHOR_X's own comment)
# -- used ONLY as a place to stand the bot out of the way WHILE `/fill`
# commands run, never as a real test position. Per explicit direction,
# confirmed live: the bot's own body standing inside/on a `/fill` target
# column blocks that specific cell from actually being filled (vanilla
# `/fill` can't place a block where a real entity is physically occupying
# the space) -- with every schematic now anchored at ORIGIN itself, a
# setup that teleported straight to ORIGIN before placing put the bot
# standing right in the middle of the very region about to be filled,
# which is exactly what caused a real "No blocks were filled" failure
# for every /fill command in the run. (-7, -7) is comfortably outside
# every schematic's own small footprint (all well under 24 blocks in
# either direction) while still safely inside this world's own bounds
# (spans to 24, 24 -- confirmed live, not guessed).
HOLDING_X = -7.0
HOLDING_Y = ORIGIN_Y
HOLDING_Z = -7.0

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
GOTO_TIMEOUT_SECONDS = 5.0
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
    await actions.teleport(ctx, *_block_center(ORIGIN_X, ORIGIN_Y, ORIGIN_Z), timeout=TELEPORT_TIMEOUT_SECONDS)


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

SCHEMATIC_TIMEOUT_SECONDS = 5.0


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
    IDLE, teleport to HOLDING (well outside every schematic's own
    footprint), place the schematic at the given anchor, THEN teleport to
    the schematic's own white-wool "start" marker (see
    litematic.WAYPOINT_BLOCK_ROLES) -- the shared shape every wool-marker
    scenario test in this file uses (see setup_goto_onto_schematic's own
    original docstring, now generalized here once a second/third
    schematic-driven test -- goto_jump/goto_impossible -- made the
    copy-pasted version worth factoring out).

    Two real, separately-confirmed-live bugs shaped this exact ordering:

    1. Teleporting STRAIGHT to `start` before anything is placed left
       nothing solid under it at teleport time (the schematic's own floor
       didn't exist yet) -- the bot fell into the void, and
       actions.teleport's own arrival check (a real 3D distance) polled
       for the full TELEPORT_TIMEOUT_SECONDS waiting for a y that was
       never going to happen, raising asyncio.TimeoutError before
       place_schematic ever ran at all.
    2. Once fixed to land at a safe spot FIRST, that spot was ORIGIN --
       but every schematic is now anchored at ORIGIN's own (x, z) too
       (see SCHEMATIC_ANCHOR_X's own comment), so the bot ended up
       standing right in the middle of the very region about to be
       filled. Confirmed live, per explicit direction: vanilla `/fill`
       can't place a block where a real entity is physically occupying
       that cell -- every `/fill` command failed with "No blocks were
       filled" for exactly this reason, every single run, regardless of
       the teleport-ordering fix above. HOLDING (well outside every
       schematic's own footprint) is genuinely out of the way, not just
       "a different fixed point."
    """
    async def setup(ctx: TestContext) -> None:
        await actions.reset_to_idle(ctx)
        await actions.teleport(ctx, *_block_center(HOLDING_X, HOLDING_Y, HOLDING_Z), timeout=TELEPORT_TIMEOUT_SECONDS)
        schematic = Schematic.from_file(schematic_path)
        await actions.place_schematic(
            ctx, schematic, anchor_x=int(anchor_x), anchor_y=int(anchor_y), anchor_z=int(anchor_z),
            timeout=SCHEMATIC_TIMEOUT_SECONDS,
        )
        start = _one(schematic.waypoints.start, "start")
        await actions.teleport(
            ctx, *_block_center(anchor_x + start.x, anchor_y + start.y, anchor_z + start.z),
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
    behind for the next one -- per explicit direction, cleanup must always
    happen, even when the run leading up to it failed.

    Sends !stop FIRST (reset_to_idle -- a fire-and-forget send, no
    poll/wait attached, so this can't reintroduce the same hang risk
    teleporting away did) -- per explicit direction, after a real live
    report: a test's own func can still be mid-!goto right up until
    run_test_case's own timeout fires it as a failure, and clearing the
    schematic's blocks out from under a bot that's still actively trying
    to walk through/around that exact region (still fighting toward its
    last commanded target) is worth stopping first, even though it isn't
    what makes cleanup itself reliable -- see below for that part.

    Deliberately does NOT teleport the bot away first, or wait/confirm
    anything about its position at all, before clearing -- an earlier
    version did (reset_to_idle + teleport(HOLDING) first, since a real
    entity standing on a `/fill` target cell blocks that one cell from
    being cleared -- see place_schematic's own docstring for the same
    fact on the placing side), but that made cleanup itself depend on
    teleport() succeeding, which is exactly the kind of thing that can
    fail (a still-active !goto fighting the /tp, a slow/lost query reply,
    ...) -- confirmed live, repeatedly: teardown's own teleport timing
    out meant clear_schematic was NEVER EVEN ATTEMPTED, leaving placed
    blocks in the world with no cleanup at all. Clearing unconditionally,
    with nothing that can itself time out in front of it, is what
    actually guarantees "even with failures it must clean the blocks" --
    the one narrow cost is that if the bot happens to be standing on
    exactly one specific cell within the schematic's own small footprint
    at the exact moment /fill runs, real vanilla /fill still clears every
    OTHER cell in the region and only skips that one occupied cell (not a
    failure of the whole fill) -- an acceptable, narrow edge case against
    the alternative of teardown sometimes doing nothing at all.
    """
    async def teardown(ctx: TestContext) -> None:
        await actions.reset_to_idle(ctx)
        schematic = Schematic.from_file(schematic_path)
        await actions.clear_schematic(
            ctx, schematic, anchor_x=int(anchor_x), anchor_y=int(anchor_y), anchor_z=int(anchor_z),
            timeout=SCHEMATIC_TIMEOUT_SECONDS,
        )
        # Fire-and-forget /tp to HOLDING, sent LAST (after clearing, not
        # before it) and never awaited/confirmed -- per explicit
        # direction: a failed test should still leave the bot at HOLDING
        # rather than wherever it ended up, so it doesn't sit in/near the
        # NEXT test's own setup area (still mid-!goto, standing on the
        # next schematic's own anchor, ...). Deliberately NOT
        # actions.teleport() (which polls !query position until arrival
        # or times out) -- that's exactly the blocking-on-confirmation
        # shape that made the OLD teardown's own teleport-before-clearing
        # step unreliable (see this function's own docstring above). This
        # is a pure best-effort nudge: send the command, don't wait to
        # see whether it landed, so it can never be the reason teardown
        # itself fails or blocks. send_teleport, not send_console_command
        # -- still fire-and-forget (see ModBridge.send_teleport's own
        # docstring: it's the same unrated, unconfirmed `_send` shape),
        # but ALSO zeros residual velocity/fall distance the instant it
        # lands, unlike a raw `/tp` sent as chat text (see actions.
        # teleport's own docstring for the real bug this avoids: leftover
        # fall velocity from THIS test's own still-active goto surviving a
        # plain /tp and silently skewing the NEXT test's own jump).
        holding_x, holding_y, holding_z = _block_center(HOLDING_X, HOLDING_Y, HOLDING_Z)
        await ctx.bridge.send_teleport(holding_x, holding_y, holding_z)

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


def _make_waypoint_goto_test(schematic_path: Path, anchor_x: float, anchor_y: float, anchor_z: float):
    """Builds a test function that sends !goto to a schematic's own "end"
    marker and asserts the bot's real walked trail passed through every
    "path" waypoint and never touched a "forbidden" one (see
    actions.goto_with_waypoints) -- the shared shape both goto_jump and
    goto_jump_2 use (gap-crossing scenarios differing only in gap width/
    whether a "path" marker above the gap is even present -- see
    goto_jump_2's own comment for why it has none). Factored out once a
    second schematic needed the exact same assertion shape
    test_goto_jumps_across_gap originally had, to avoid copy-pasting it a
    second time (same reasoning _make_schematic_setup/_make_schematic_teardown
    were factored out for their own shared shape).
    """
    async def test(ctx: TestContext) -> None:
        schematic = Schematic.from_file(schematic_path)
        end = _one(schematic.waypoints.end, "end")
        anchor = (anchor_x, anchor_y, anchor_z)

        result = await actions.goto_with_waypoints(
            ctx,
            target_x=anchor_x + end.x,
            target_y=anchor_y + end.y,
            target_z=anchor_z + end.z,
            distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
            timeout=GOTO_TIMEOUT_SECONDS,
            path=[_offset(*anchor, w) for w in schematic.waypoints.path],
            forbidden=[_offset(*anchor, w) for w in schematic.waypoints.forbidden],
        )

        for i, hits in enumerate(result.path_hits):
            assert hits > 0, f"never walked through path waypoint {schematic.waypoints.path[i]}"
        for i, hits in enumerate(result.forbidden_hits):
            assert hits == 0, f"fell into forbidden waypoint {schematic.waypoints.forbidden[i]}"

    return test


test_goto_jumps_across_gap = _make_waypoint_goto_test(JUMP_SCHEMATIC_PATH, JUMP_SCHEMATIC_ANCHOR_X, JUMP_SCHEMATIC_ANCHOR_Y, JUMP_SCHEMATIC_ANCHOR_Z)


# A wider (2-block) gap-jump scenario -- same shape as goto_jump, no
# "path" waypoint (per explicit direction: the gap is still within real
# jump range, and unlike a 1-block gap there's no single point directly
# "above" a 2-block gap that the bot is required to pass exactly through
# -- both gap columns are only marked "forbidden", asserting the bot
# never falls into either one on its way to "end").
JUMP2_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_jump_2.litematic"
JUMP2_SCHEMATIC_ANCHOR_X = ORIGIN_X
JUMP2_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
JUMP2_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_jump_2 = _make_schematic_setup(JUMP2_SCHEMATIC_PATH, JUMP2_SCHEMATIC_ANCHOR_X, JUMP2_SCHEMATIC_ANCHOR_Y, JUMP2_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_jump_2 = _make_schematic_teardown(JUMP2_SCHEMATIC_PATH, JUMP2_SCHEMATIC_ANCHOR_X, JUMP2_SCHEMATIC_ANCHOR_Y, JUMP2_SCHEMATIC_ANCHOR_Z)
test_goto_jumps_across_wider_gap = _make_waypoint_goto_test(JUMP2_SCHEMATIC_PATH, JUMP2_SCHEMATIC_ANCHOR_X, JUMP2_SCHEMATIC_ANCHOR_Y, JUMP2_SCHEMATIC_ANCHOR_Z)


# A longer course (8 blocks) combining both goto_jump's own "path"
# checkpoint above a gap AND goto_jump_2's own "forbidden" gap columns --
# same shared shape (_make_waypoint_goto_test), just a longer/more
# demanding run: a required path waypoint at z=4, a 2-block forbidden gap
# at z=2/z=3, and a separate forbidden marker at z=0 past the end.
JUMP3_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_jump_3.litematic"
JUMP3_SCHEMATIC_ANCHOR_X = ORIGIN_X
JUMP3_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
JUMP3_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_jump_3 = _make_schematic_setup(JUMP3_SCHEMATIC_PATH, JUMP3_SCHEMATIC_ANCHOR_X, JUMP3_SCHEMATIC_ANCHOR_Y, JUMP3_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_jump_3 = _make_schematic_teardown(JUMP3_SCHEMATIC_PATH, JUMP3_SCHEMATIC_ANCHOR_X, JUMP3_SCHEMATIC_ANCHOR_Y, JUMP3_SCHEMATIC_ANCHOR_Z)
test_goto_jumps_across_gap_with_checkpoint = _make_waypoint_goto_test(JUMP3_SCHEMATIC_PATH, JUMP3_SCHEMATIC_ANCHOR_X, JUMP3_SCHEMATIC_ANCHOR_Y, JUMP3_SCHEMATIC_ANCHOR_Z)


# Another gap-jump course, same shared shape as goto_jump_3: a required
# path checkpoint at z=4 above the gap and a wider (5-block, z=2..z=6)
# forbidden gap run underneath it.
JUMP4_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_jump_4.litematic"
JUMP4_SCHEMATIC_ANCHOR_X = ORIGIN_X
JUMP4_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
JUMP4_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_jump_4 = _make_schematic_setup(JUMP4_SCHEMATIC_PATH, JUMP4_SCHEMATIC_ANCHOR_X, JUMP4_SCHEMATIC_ANCHOR_Y, JUMP4_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_jump_4 = _make_schematic_teardown(JUMP4_SCHEMATIC_PATH, JUMP4_SCHEMATIC_ANCHOR_X, JUMP4_SCHEMATIC_ANCHOR_Y, JUMP4_SCHEMATIC_ANCHOR_Z)
test_goto_jumps_across_gap_4 = _make_waypoint_goto_test(JUMP4_SCHEMATIC_PATH, JUMP4_SCHEMATIC_ANCHOR_X, JUMP4_SCHEMATIC_ANCHOR_Y, JUMP4_SCHEMATIC_ANCHOR_Z)


# A gap wider than the bot's real jump range -- fills the "goto across a
# gap wider than jump range" pending item (distinct from
# goto_impossible_1's horizontal-wall case): only a magenta "unreachable"
# marker on the far side, no "end"/"path"/"forbidden" waypoints, since the
# gap itself (not a specific in-gap column) is what makes it unreachable.
# Same test shape as goto_impossible_1 -- asserts the bot never arrives,
# per explicit direction the test should fail if it does.
JUMP5_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_jump_5.litematic"
JUMP5_SCHEMATIC_ANCHOR_X = ORIGIN_X
JUMP5_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
JUMP5_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_jump_5 = _make_schematic_setup(JUMP5_SCHEMATIC_PATH, JUMP5_SCHEMATIC_ANCHOR_X, JUMP5_SCHEMATIC_ANCHOR_Y, JUMP5_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_jump_5 = _make_schematic_teardown(JUMP5_SCHEMATIC_PATH, JUMP5_SCHEMATIC_ANCHOR_X, JUMP5_SCHEMATIC_ANCHOR_Y, JUMP5_SCHEMATIC_ANCHOR_Z)


async def test_goto_never_crosses_gap_too_wide(ctx: TestContext) -> None:
    """Sends !goto toward goto_jump_5's own magenta "unreachable" marker,
    across a gap wider than the bot's real jump range, and asserts the
    bot never actually gets there (see actions.assert_goto_never_arrives)
    -- proves the mod recognizes the gap as unreachable rather than
    attempting and failing the jump.
    """
    schematic = Schematic.from_file(JUMP5_SCHEMATIC_PATH)
    unreachable = _one(schematic.waypoints.unreachable, "unreachable")

    await actions.assert_goto_never_arrives(
        ctx,
        target_x=JUMP5_SCHEMATIC_ANCHOR_X + unreachable.x,
        target_y=JUMP5_SCHEMATIC_ANCHOR_Y + unreachable.y,
        target_z=JUMP5_SCHEMATIC_ANCHOR_Z + unreachable.z,
        distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
        timeout=IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    )


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
# so long every full pytest run pays for it needlessly. Kept at 5s per
# explicit direction (every goto-related timeout in this file -> 5s) --
# LegsGotoNode now gives up on a confirmed NO_PATH within a tick or two
# (see its own isFinished() fix), so 5s is still generous for "confirm it
# never arrived" even though there's no real arrival to wait out here,
# just a fixed observation window.
IMPOSSIBLE_GOTO_WINDOW_SECONDS = 5.0

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


# A target genuinely blocked only by HEAD clearance -- oak_leaves float at
# schematic y=2 directly above a walkable y=0/y=1 floor, so unlike
# goto_impossible_1's horizontal wall, there's no obstacle at foot height
# at all; the player's own 2-block-tall hitbox is what makes the space
# under the leaves unenterable. Marked "unreachable" for the same
# assert_goto_never_arrives reasoning goto_impossible_1 uses -- but the
# real thing this scenario exists to catch is different: a pathfinder
# that only checks the DESTINATION cell's headroom (not the full path
# leading to it) could wrongly treat this as "just needs a jump" and
# attempt one anyway, so this test also asserts zero real jumps occurred
# (see actions.assert_never_jumps), not just that arrival never happened.
LEAVES_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_leaves_1.litematic"
LEAVES_SCHEMATIC_ANCHOR_X = ORIGIN_X
LEAVES_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
LEAVES_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_leaves_1 = _make_schematic_setup(LEAVES_SCHEMATIC_PATH, LEAVES_SCHEMATIC_ANCHOR_X, LEAVES_SCHEMATIC_ANCHOR_Y, LEAVES_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_leaves_1 = _make_schematic_teardown(LEAVES_SCHEMATIC_PATH, LEAVES_SCHEMATIC_ANCHOR_X, LEAVES_SCHEMATIC_ANCHOR_Y, LEAVES_SCHEMATIC_ANCHOR_Z)


async def test_goto_never_jumps_at_leaves(ctx: TestContext) -> None:
    """Sends !goto toward goto_leaves_1's own magenta "unreachable" marker
    (blocked by head clearance under floating leaves, not a horizontal
    wall) and asserts BOTH that the bot never arrives AND that it never
    attempts a real jump trying to force its way there -- see this
    schematic's own comment above for why a jump attempt specifically
    (not just eventual arrival) is what a head-clearance-only obstacle
    scenario needs to rule out.
    """
    schematic = Schematic.from_file(LEAVES_SCHEMATIC_PATH)
    unreachable = _one(schematic.waypoints.unreachable, "unreachable")

    await actions.assert_never_jumps(
        ctx,
        during=actions.assert_goto_never_arrives(
            ctx,
            target_x=LEAVES_SCHEMATIC_ANCHOR_X + unreachable.x,
            target_y=LEAVES_SCHEMATIC_ANCHOR_Y + unreachable.y,
            target_z=LEAVES_SCHEMATIC_ANCHOR_Z + unreachable.z,
            distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
            timeout=IMPOSSIBLE_GOTO_WINDOW_SECONDS,
        ),
    )


# A reachable room with the same floating-leaves fixture as goto_leaves_1,
# but here the leaves don't block the route to "end" -- the bot must walk
# through the required "path" checkpoint, avoid every "forbidden" wall
# cell, and jump exactly once along the way (per explicit direction).
LEAVES2_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_leaves_2.litematic"
LEAVES2_SCHEMATIC_ANCHOR_X = ORIGIN_X
LEAVES2_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
LEAVES2_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_leaves_2 = _make_schematic_setup(LEAVES2_SCHEMATIC_PATH, LEAVES2_SCHEMATIC_ANCHOR_X, LEAVES2_SCHEMATIC_ANCHOR_Y, LEAVES2_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_leaves_2 = _make_schematic_teardown(LEAVES2_SCHEMATIC_PATH, LEAVES2_SCHEMATIC_ANCHOR_X, LEAVES2_SCHEMATIC_ANCHOR_Y, LEAVES2_SCHEMATIC_ANCHOR_Z)

async def test_goto_leaves_2_reaches_goal_with_one_jump(ctx: TestContext) -> None:
    """Sends !goto toward goto_leaves_2's own "end" marker and asserts the
    bot's real walked trail passed through the required "path" waypoint,
    never touched a "forbidden" wall cell, AND jumped exactly once along
    the way (see actions.assert_jumps_done) -- per explicit direction.
    """
    schematic = Schematic.from_file(LEAVES2_SCHEMATIC_PATH)
    end = _one(schematic.waypoints.end, "end")
    anchor = (LEAVES2_SCHEMATIC_ANCHOR_X, LEAVES2_SCHEMATIC_ANCHOR_Y, LEAVES2_SCHEMATIC_ANCHOR_Z)

    async def _run() -> None:
        result = await actions.goto_with_waypoints(
            ctx,
            target_x=LEAVES2_SCHEMATIC_ANCHOR_X + end.x,
            target_y=LEAVES2_SCHEMATIC_ANCHOR_Y + end.y,
            target_z=LEAVES2_SCHEMATIC_ANCHOR_Z + end.z,
            distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
            timeout=GOTO_TIMEOUT_SECONDS,
            path=[_offset(*anchor, w) for w in schematic.waypoints.path],
            forbidden=[_offset(*anchor, w) for w in schematic.waypoints.forbidden],
        )

        for i, hits in enumerate(result.path_hits):
            assert hits > 0, f"never walked through path waypoint {schematic.waypoints.path[i]}"
        for i, hits in enumerate(result.forbidden_hits):
            assert hits == 0, f"walked through forbidden waypoint {schematic.waypoints.forbidden[i]}"

    await actions.assert_jumps_done(ctx, during=_run(), count=1)


# A stairs-climbing course -- start at the bottom (y=1), end 3 blocks
# higher (y=4) up a run of stairs, forbidden markers lining the ground on
# both sides of the staircase (falling off is a failure). Fills the
# pending "goto up/down stairs or slabs" item: half-height terrain the
# legs state machine should climb by stepping, not by triggering a real
# jump (see count_jumps' own docstring for why a y-rise alone isn't
# treated as a jump) -- this test asserts that directly with
# assert_never_jumps on top of the usual path/forbidden waypoint checks.
STAIRS_SCHEMATIC_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "schematics" / "goto_stairs_1.litematic"
STAIRS_SCHEMATIC_ANCHOR_X = ORIGIN_X
STAIRS_SCHEMATIC_ANCHOR_Y = ORIGIN_Y
STAIRS_SCHEMATIC_ANCHOR_Z = ORIGIN_Z

setup_goto_stairs_1 = _make_schematic_setup(STAIRS_SCHEMATIC_PATH, STAIRS_SCHEMATIC_ANCHOR_X, STAIRS_SCHEMATIC_ANCHOR_Y, STAIRS_SCHEMATIC_ANCHOR_Z)
teardown_clear_goto_stairs_1 = _make_schematic_teardown(STAIRS_SCHEMATIC_PATH, STAIRS_SCHEMATIC_ANCHOR_X, STAIRS_SCHEMATIC_ANCHOR_Y, STAIRS_SCHEMATIC_ANCHOR_Z)

# Longer than the standard GOTO_TIMEOUT_SECONDS (5s) -- a 3-block climb up
# a staircase run takes more real ticks than a flat-ground or single-gap-
# jump course before reaching "end".
STAIRS_GOTO_TIMEOUT_SECONDS = 10.0


async def test_goto_climbs_stairs_without_jumping(ctx: TestContext) -> None:
    """Sends !goto up goto_stairs_1's own staircase to the "end" marker
    and asserts the bot's real walked trail never touched a forbidden
    ground-level cell on either side AND never performed a real jump
    climbing the stairs (see actions.assert_never_jumps).
    """
    schematic = Schematic.from_file(STAIRS_SCHEMATIC_PATH)
    end = _one(schematic.waypoints.end, "end")
    anchor = (STAIRS_SCHEMATIC_ANCHOR_X, STAIRS_SCHEMATIC_ANCHOR_Y, STAIRS_SCHEMATIC_ANCHOR_Z)

    async def _run() -> None:
        result = await actions.goto_with_waypoints(
            ctx,
            target_x=STAIRS_SCHEMATIC_ANCHOR_X + end.x,
            target_y=STAIRS_SCHEMATIC_ANCHOR_Y + end.y,
            target_z=STAIRS_SCHEMATIC_ANCHOR_Z + end.z,
            distance_tolerance=GOTO_ARRIVAL_TOLERANCE,
            timeout=STAIRS_GOTO_TIMEOUT_SECONDS,
            path=[_offset(*anchor, w) for w in schematic.waypoints.path],
            forbidden=[_offset(*anchor, w) for w in schematic.waypoints.forbidden],
        )

        for i, hits in enumerate(result.forbidden_hits):
            assert hits == 0, f"walked through forbidden waypoint {schematic.waypoints.forbidden[i]}"

    await actions.assert_never_jumps(ctx, during=_run())


def register_default_tests(registry: TestRegistry) -> None:
    registry.register(TestCase(
        name="goto",
        description="Sends the bot 5 blocks away via !goto and asserts it actually arrives.",
        func=test_goto_moves_bot_to_target,
        setup=setup_goto,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
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
        name="goto_jump_1",
        description="Places a schematic with a one-block gap, sends !goto across it, and asserts the bot jumped over the gap without falling in.",
        func=test_goto_jumps_across_gap,
        setup=setup_goto_jump,
        teardown=teardown_clear_goto_jump,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_jump_2",
        description="Places a schematic with a two-block gap, sends !goto across it, and asserts the bot jumped over the gap without falling into either forbidden column.",
        func=test_goto_jumps_across_wider_gap,
        setup=setup_goto_jump_2,
        teardown=teardown_clear_goto_jump_2,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_jump_3",
        description="Places a longer schematic with a required path checkpoint and a two-block forbidden gap, sends !goto across it, and asserts the bot walked through the checkpoint without falling into either forbidden column.",
        func=test_goto_jumps_across_gap_with_checkpoint,
        setup=setup_goto_jump_3,
        teardown=teardown_clear_goto_jump_3,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_jump_4",
        description="Places a longer schematic with a required path checkpoint and a five-block forbidden gap, sends !goto across it, and asserts the bot walked through the checkpoint without falling into any forbidden column.",
        func=test_goto_jumps_across_gap_4,
        setup=setup_goto_jump_4,
        teardown=teardown_clear_goto_jump_4,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_jump_5",
        description="Places a schematic with a gap wider than the bot's real jump range, sends !goto across it, and asserts the bot never reaches the far side.",
        func=test_goto_never_crosses_gap_too_wide,
        setup=setup_goto_jump_5,
        teardown=teardown_clear_goto_jump_5,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_stairs_1",
        description="Places a staircase schematic, sends !goto up it, and asserts the bot reaches the top without falling off either side and without performing a real jump.",
        func=test_goto_climbs_stairs_without_jumping,
        setup=setup_goto_stairs_1,
        teardown=teardown_clear_goto_stairs_1,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + STAIRS_GOTO_TIMEOUT_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_impossible",
        description="Places a schematic with a genuinely blocked target, sends !goto toward it, and asserts the bot never actually reaches it.",
        func=test_goto_never_reaches_unreachable_target,
        setup=setup_goto_impossible,
        teardown=teardown_clear_goto_impossible,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_leaves_1",
        description="Places a schematic with a target blocked only by head clearance under floating leaves, sends !goto toward it, and asserts the bot never reaches it AND never attempts a jump.",
        func=test_goto_never_jumps_at_leaves,
        setup=setup_goto_leaves_1,
        teardown=teardown_clear_goto_leaves_1,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + IMPOSSIBLE_GOTO_WINDOW_SECONDS,
    ))
    registry.register(TestCase(
        name="goto_leaves_2",
        description="Places a reachable room with the same floating-leaves fixture, sends !goto to the goal, and asserts the bot walked the required checkpoint, avoided every forbidden wall cell, and jumped exactly once.",
        func=test_goto_leaves_2_reaches_goal_with_one_jump,
        setup=setup_goto_leaves_2,
        teardown=teardown_clear_goto_leaves_2,
        timeout_seconds=TELEPORT_TIMEOUT_SECONDS + SCHEMATIC_TIMEOUT_SECONDS + GOTO_TIMEOUT_SECONDS,
    ))
