"""Shared session-scoped fixture for the in-game integration suite --
launches ONE real Minecraft client for the whole test session (per
explicit direction: "I imagine open one single client per run, so
multiple tests can run in that client"), not one launch per test. Every
test in this directory shares that single running client/connection;
each test is responsible for its own scenario setup/teardown around it
(see individual test files' own fixtures).

This is the automated/unattended entry point into the same test bodies
minebot-mod's TESTING.md's "!runtest" chat command also runs manually --
see that doc's own "Two entry points into the same test bodies" section.
Nothing here should ever be triggered via chat/!runtest, and !runtest
must never invoke anything in this directory -- they are two independent
ways to run the same underlying test logic (minebot/testing/tests.py),
kept deliberately separate.

Requires:
- A minebot-mod checkout at MINEBOT_MOD_REPO_PATH (same env var/default
  minebot/config.py already uses for the build-staleness check).
- A real display for the launched client to render into -- DISPLAY must
  already be set in the environment this pytest process runs in (see
  minebot-mod's TESTING.md "Launch stability" section for why a real
  WSLg display, not xvfb-run, is what's actually used here: the user
  needs to be able to watch the client, not just get a pass/fail).
Genuinely disposable, regenerated fresh on every run -- this fixture
deletes any existing run/saves/<world> directory and launches with
`-Pminebot.bootstrapTestWorld=<name>` (not a plain quickplay re-join of
whatever was there before), so no state from a previous automated run OR
a manual play session (someone taking control and dying/digging, say) can
ever leak into the next run. Regenerating a fresh flat world costs a few
extra seconds over a plain quickplay re-join, but "the world tests run
against might not actually be clean" is exactly the kind of intermittent,
hard-to-diagnose failure this trades away that cost to avoid -- confirmed
live: a real leftover DeathWatcher.DEATH_POSITION from a manual death
silently hung an automated run, since minebot-mod's LegsStateMachine
ranks death-recovery above !goto's own GOTO state, so the stale recovery
simply never yielded control to the test at all.

Not collected/run by a plain `uv run pytest` from the repo root -- pytest
only descends into directories it's pointed at or that contain files
matching its test discovery under the given rootdir/testpaths, and this
suite is slow/environment-dependent enough (real client launch, real
GPU, ~15-20s startup) that it must be run explicitly:
    uv run pytest tests/integration/
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from minebot.bridge.client import ModBridge, ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.query import QueryResultTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.mod_version import expected_commit
from minebot.testing import actions
from minebot.testing.replay import ReplayRecorder
from minebot.testing.runner import TestContext

log = logging.getLogger("minebot.tests.integration")

MOD_REPO_PATH = Path(os.environ.get("MINEBOT_MOD_REPO_PATH", "/home/colaila/git/mods/minebot-mod"))
TEST_WORLD_NAME = os.environ.get("MINEBOT_TEST_WORLD", "minebot-test-world")
# Opt-in -- most local/CI runs don't want the per-tick replay_frame traffic
# or the resulting JSON files. Set MINEBOT_RECORD_REPLAY=true to record.
RECORD_REPLAY = os.environ.get("MINEBOT_RECORD_REPLAY", "false").lower() in ("1", "true", "yes")

# Real client boot + world load + control-channel handshake, confirmed
# live to take well under this during minebot-mod's TESTING.md
# investigation -- generous on top of that so a slow/cold Gradle daemon
# start doesn't flake the whole session.
CLIENT_STARTUP_TIMEOUT_SECONDS = 90.0
# How long to wait for the launched client process to actually exit once
# asked to -- Fabric/LWJGL clients don't always tear down instantly.
CLIENT_SHUTDOWN_TIMEOUT_SECONDS = 15.0


def _free_tcp_port() -> int:
    """An OS-assigned ephemeral port, released immediately after -- same
    "ask the OS, then reuse the number" approach test_mod_bridge.py uses
    via ModBridge's own port=0 binding, but needed here as a real number
    up front since it has to be baked into the Gradle command line before
    ModBridge itself binds anything.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _delete_stale_test_world() -> None:
    """Removes any existing run/saves/<world> directory before launch --
    see this module's own docstring for why regenerating fresh every run
    (not reusing/copy-restoring a previous save) is what actually keeps
    this suite disposable. A no-op the very first time this ever runs
    (nothing to delete yet); TestWorldBootstrap creates the directory
    fresh either way.
    """
    world_dir = MOD_REPO_PATH / "run" / "saves" / TEST_WORLD_NAME
    if world_dir.exists():
        log.info("integration session: removing stale test world at %s", world_dir)
        shutil.rmtree(world_dir)


class IngameSession:
    """Owns the launched client subprocess + the ModBridge connected to
    it, plus the same trackers run_loop.py normally keeps live (so test
    bodies written against TestContext -- the same ones !runtest also
    runs -- work completely unmodified here).
    """

    def __init__(self, bridge: ModBridge, ctx: TestContext, client_process: subprocess.Popen, reader_task: asyncio.Task) -> None:
        self.bridge = bridge
        self.ctx = ctx
        self._client_process = client_process
        self._reader_task = reader_task

    async def close(self) -> None:
        self._reader_task.cancel()
        try:
            await self._reader_task
        except (asyncio.CancelledError, Exception):
            pass
        await self.bridge.close()
        _terminate_client(self._client_process)


def _terminate_client(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    log.info("integration session: terminating client process (pid=%s)", process.pid)
    process.terminate()
    try:
        process.wait(timeout=CLIENT_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.warning("integration session: client didn't exit in %.0fs, killing", CLIENT_SHUTDOWN_TIMEOUT_SECONDS)
        process.kill()
        process.wait(timeout=CLIENT_SHUTDOWN_TIMEOUT_SECONDS)


async def _read_events_forever(
    events: AsyncIterator[ModEvent], tracker: EntityTracker, self_position: SelfPositionTracker,
    query_result: QueryResultTracker, replay_recorder: ReplayRecorder,
) -> None:
    """Minimal version of run_loop.py's own _read_events -- this suite has
    no chat-command dispatch to feed (nothing here is driven by chat, see
    this module's own docstring), so there's no queue/consumer split
    needed, just keeping the trackers caught up so test bodies polling
    ctx.self_position/ctx.tracker always see live state.

    Takes an already-created `events` iterator rather than calling
    bridge.events() itself -- ModBridge.events() iterates the raw
    WebSocket connection object directly (`async for raw in connection`),
    which only one consumer can ever drain: two independent bridge.
    events() calls each create their OWN generator racing to read the
    same underlying connection, silently splitting/dropping messages
    between whichever one happens to be awaiting next (confirmed live:
    the very first version of this fixture called bridge.events() once
    here for the initial `hello` check and again inside this function,
    and every event after `hello` -- including every `position` broadcast
    -- vanished into whichever generator's pending read won the race,
    hanging test_goto_moves_bot_to_target forever waiting on a
    self_position that was never actually going to update). The fixture
    below creates exactly ONE bridge.events() generator and passes it to
    both the `hello` check and this function.
    """
    async for event in events:
        if event.type == "position":
            self_position.handle_event(event)
        elif event.type in ("entity", "death", "respawn"):
            tracker.handle_event(event)
        elif event.type == "query_result":
            query_result.handle_event(event)
        elif event.type == "replay_frame":
            replay_recorder.handle_event(event)


@pytest_asyncio.fixture(scope="session")
async def ingame_session():
    if not MOD_REPO_PATH.is_dir():
        pytest.skip(f"minebot-mod checkout not found at {MOD_REPO_PATH} (set MINEBOT_MOD_REPO_PATH)")
    if "DISPLAY" not in os.environ:
        pytest.skip("no DISPLAY set -- this suite launches a real, visible client (see this file's own docstring)")

    control_port = _free_tcp_port()

    _delete_stale_test_world()

    bridge = ModBridge("127.0.0.1", control_port)
    connect_task = asyncio.ensure_future(bridge.connect())
    while bridge._server is None:
        await asyncio.sleep(0.01)

    # bootstrapTestWorld, NOT quickPlayWorld -- confirmed live during
    # minebot-mod's TESTING.md investigation that TestWorldBootstrap's own
    # WorldOpenFlows.createFreshLevel call already calls Minecraft.
    # doWorldLoad itself, i.e. world CREATION already joins the world in
    # the same launch; a separate quickplay re-join afterward isn't
    # needed. Using bootstrapTestWorld here (not just on some separate
    # one-time setup step) is exactly what keeps this suite honestly
    # disposable: a manual play session (a real death/digging left
    # DeathWatcher.DEATH_POSITION and dug blocks behind, confirmed live --
    # see this file's own docstring) previously left EXACTLY this kind of
    # leftover state sitting in run/saves/minebot-test-world, which then
    # silently hung a later automated run (LegsStateMachine's own edges
    # rank GO_TO_DEATH_POSITION/PICKUP_ITEMS above GOTO, so the stale
    # death recovery simply never let !goto's own test ever take over).
    log.info("integration session: launching client (control port %s)", control_port)
    gradlew_args = [
        "./gradlew", "runClient",
        f"-Pminebot.bootstrapTestWorld={TEST_WORLD_NAME}",
        f"-Pminebot.controlPort={control_port}",
    ]
    if RECORD_REPLAY:
        gradlew_args.append("-Pminebot.recordReplay=true")
    client_process = subprocess.Popen(
        gradlew_args,
        cwd=MOD_REPO_PATH,
        env=os.environ.copy(),
    )

    try:
        await asyncio.wait_for(connect_task, timeout=CLIENT_STARTUP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _terminate_client(client_process)
        pytest.fail(f"client never connected within {CLIENT_STARTUP_TIMEOUT_SECONDS:.0f}s")

    # First real event off the wire should be `hello` -- confirms the mod
    # actually initialized and the control channel handshake completed,
    # not just that a TCP connection opened (see minebot-mod's TESTING.md
    # "First slice" for why this specific check matters: it's the
    # cheapest possible proof the WHOLE chain -- client up, mod
    # initialized, control channel connected, event broadcast working --
    # is actually alive, not just partially).
    events = bridge.events()
    hello = await asyncio.wait_for(events.__anext__(), timeout=CLIENT_STARTUP_TIMEOUT_SECONDS)
    if hello.type != "hello":
        _terminate_client(client_process)
        pytest.fail(f"expected 'hello' as the first event, got {hello.type!r}")
    log.info("integration session: client connected (%s)", hello.data)

    tracker = EntityTracker()
    self_position = SelfPositionTracker()
    query_result = QueryResultTracker()
    replay_recorder = ReplayRecorder()
    # Reuses the SAME `events` generator the `hello` check just consumed
    # from -- see _read_events_forever's own docstring for why a second,
    # independent bridge.events() call here would silently race it for
    # the same underlying connection instead.
    reader_task = asyncio.ensure_future(_read_events_forever(events, tracker, self_position, query_result, replay_recorder))

    ctx = TestContext(
        bridge=bridge, self_position=self_position, tracker=tracker, query_result=query_result,
        replay_recorder=replay_recorder, mod_commit=expected_commit(str(MOD_REPO_PATH)),
    )
    session = IngameSession(bridge=bridge, ctx=ctx, client_process=client_process, reader_task=reader_task)

    # `hello` only proves the control channel connected and the mod class
    # initialized (MinebotMod.onControlChannelConnected fires it unconditionally,
    # independent of world/player state) -- it fires well before
    # client.player actually exists, since world creation/join is still in
    # flight at that point. Querying `position` before the local player
    # exists fails with "no local player yet" (see MinebotMod's onTick
    # early-return), so poll until a real position comes back instead of
    # handing control to the first test immediately after `hello`.
    #
    # A successful query is not enough on its own, though -- confirmed
    # live: MinebotMod's "position" query only null-checks
    # Minecraft.getInstance().player, and a freshly-constructed LocalPlayer
    # (non-null, but not yet handed a real spawn packet from the
    # integrated server) reads back a default (0, 0, 0), which is not a
    # real in-world position at all. The very first test's own /tp landed
    # fine, but by the time its own position broadcast arrived the real
    # spawn packet had already overwritten it back to the bootstrap
    # world's actual spawn point (8.5, -60, 2.5), i.e. the /tp fired
    # before the player was actually stably in the world, and got clobbered
    # by spawn finishing a moment later. Requiring the SAME real
    # (non-origin-default) position on two consecutive polls confirms the
    # player has actually settled into the world, not just that the query
    # stopped erroring.
    log.info("integration session: waiting for local player to spawn")
    deadline = asyncio.get_event_loop().time() + CLIENT_STARTUP_TIMEOUT_SECONDS
    last_position: tuple[float, float, float] | None = None
    while True:
        try:
            pos = await actions.query_position(ctx, timeout=5.0)
        except (RuntimeError, asyncio.TimeoutError):
            last_position = None
        else:
            current = (pos.x, pos.y, pos.z)
            if current != (0.0, 0.0, 0.0) and current == last_position:
                break
            last_position = current
        if asyncio.get_event_loop().time() >= deadline:
            _terminate_client(client_process)
            pytest.fail(f"local player never spawned within {CLIENT_STARTUP_TIMEOUT_SECONDS:.0f}s")
        await asyncio.sleep(0.5)
    log.info("integration session: local player spawned, starting tests")

    # Force the world's own player out of CREATIVE before running any real
    # test -- see actions.wait_for_gamemode's own docstring and MinebotMod's
    # "gamemode" case docstring for the real bug this fixes: a creative
    # test world lets vanilla's own double-tap-space-toggles-flying
    # detection turn a real jump-retry into permanent, ungoverned flight,
    # which previously looked exactly like a permanent physics wedge in
    # goto_leaves_2. Session-scoped (this fixture, not any individual
    # test's own setup) since gamemode is world/session state. Saves
    # whatever gamemode the world actually started in first and restores
    # it in the `finally` below (not hardcoded back to "creative") --
    # TestWorldBootstrap's own world generation already requests creative
    # for THIS fixture's own disposable world, but this fixture is shared
    # code any other session (a real LAN world with its own, possibly
    # different, starting gamemode) could reasonably use too.
    original_gamemode = await actions.query(ctx, "gamemode", timeout=CLIENT_STARTUP_TIMEOUT_SECONDS)
    log.info("integration session: switching to survival (was %s)", original_gamemode)
    await actions.wait_for_gamemode(ctx, "survival", timeout=CLIENT_STARTUP_TIMEOUT_SECONDS)
    log.info("integration session: survival confirmed, starting tests")

    try:
        yield session
    finally:
        # Best-effort -- swallow any failure (a dead control channel from
        # a crashed client, a closed connection, ...) rather than letting
        # a failed restore attempt mask whatever real exception the `try`
        # body raised, or skip session.close() entirely below.
        try:
            log.info("integration session: restoring gamemode to %s", original_gamemode)
            await actions.wait_for_gamemode(ctx, original_gamemode, timeout=CLIENT_STARTUP_TIMEOUT_SECONDS)
        except Exception:
            log.exception("integration session: failed to restore gamemode to %s", original_gamemode)
        await session.close()


@pytest_asyncio.fixture
async def ctx(ingame_session: IngameSession) -> AsyncIterator[TestContext]:
    """Per-test alias for the shared session's TestContext -- exists so
    individual test files can depend on `ctx` directly (matching
    !runtest's own test-function signature, `func(ctx: TestContext)`)
    without every test needing to know about IngameSession itself.

    A `yield` fixture (not a plain `return`) specifically so code AFTER
    the yield runs as teardown, once per test, regardless of whether the
    test passed or failed -- the standard pytest idiom for "assert clean
    end-state" (see FINDINGS.md/this repo's own CLAUDE.md for other uses
    of the same shape). Asserts every peer state machine (player_intention/
    legs/hands/head) has actually settled back to IDLE after each test --
    the shared check every test in this directory gets for free just by
    depending on `ctx`, rather than each test writing its own copy.
    Deliberately NOT each test's own registered TestCase.teardown (see
    minebot/testing/runner.py's own docstring for that hook) -- teardown
    there is about restoring WORLD state a test itself changed (clearing
    placed blocks, see actions.clear_schematic), a per-test concern only
    the test itself knows the shape of; this is a single, repo-wide
    invariant every test should hold on exit, independent of whatever
    state it individually needed to set up.

    Exists specifically because this class of bug (a state machine
    silently left in a non-idle state after a command that should have
    ended it) went unnoticed for as long as it did -- see
    LegsStateMachine's own isStopCommand fix -- purely because nothing
    could assert against real final state before minebot/testing/query.py
    existed. If a future test genuinely needs to end in a non-IDLE state
    (e.g. asserting DEFEND stays active across a respawn -- see
    PlayerIntentionState's own docstring), it should NOT depend on this
    fixture for that assertion; a different, test-specific check is more
    honest than special-casing an exception into a supposedly-universal
    invariant.
    """
    yield ingame_session.ctx

    await actions.assert_state(ingame_session.ctx, "player_intention", "IDLE")
    await actions.assert_state(ingame_session.ctx, "legs", "IDLE")
    await actions.assert_state(ingame_session.ctx, "hands", "IDLE")
    await actions.assert_state(ingame_session.ctx, "head", "IDLE")
