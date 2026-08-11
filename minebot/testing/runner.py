"""In-game test runner -- backs !runtest (see minebot-mod's own TESTING.md
for the design this implements). Unlike `uv run pytest`, these tests don't
launch a fresh client at all: they run against whatever bot is ALREADY
connected and visible, driven the exact same way a human typing chat
commands drives it (bridge.send_goto, polling self_position, ...) -- per
explicit direction, so a second Prism instance can be joined as a
spectator and !runtest typed while watching the bot perform the test live,
with no separate scripted-launch harness needed for this path. The
launch-a-fresh-headless-client tier described in minebot-mod's TESTING.md
is a different, complementary thing (CI/unattended), not replaced by this.

A "test" here is just an async function taking a TestContext and either
returning normally (pass) or raising (fail, with the exception's own
message as the failure reason) -- deliberately not a class hierarchy or a
pytest-style framework, since the whole test body is usually a handful of
bridge sends and self_position polls, not enough machinery to justify one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.query import QueryResultTracker
from minebot.bridge.self_position import SelfPositionTracker

log = logging.getLogger("minebot.testing")


@dataclass
class TestContext:
    # Tells pytest's collector this is plain data, not a test class, despite
    # the `Test` prefix (pytest's own documented opt-out) -- without this,
    # `uv run pytest --collect-only` emits a PytestCollectionWarning every
    # time this class is imported anywhere collection walks.
    __test__ = False

    bridge: ModBridge
    self_position: SelfPositionTracker
    tracker: EntityTracker
    query_result: QueryResultTracker


TestFunc = Callable[[TestContext], Awaitable[None]]


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class -- see TestContext's own comment

    name: str
    description: str
    func: TestFunc
    # Per explicit direction, kept short (not the original 60s) -- these
    # still drive a real client over a real network round-trip, but a
    # short flat-ground walk (the only kind of scenario tests currently
    # build) should complete well under this; a test still running past
    # it is almost certainly actually stuck (bad target, blocked path,
    # stale world state), not just slow, and a short timeout surfaces
    # that fast instead of burning minutes waiting it out.
    timeout_seconds: float = 20.0
    # Optional -- runs before `func`, inside the SAME timeout_seconds
    # budget (not a separate one), so a stuck setup still fails fast
    # rather than getting its own generous allowance on top of the test's
    # own. Per explicit direction: tests should start from a known, fixed
    # position rather than "wherever the bot happened to be left standing
    # by whatever ran before it" -- e.g. `!goto`'s own setup teleports to
    # a fixed origin via actions.teleport before every run, so the test
    # itself is reproducible regardless of run order or a previous test's
    # own end state. None means no setup needed (not every test has one).
    setup: TestFunc | None = None
    # Optional -- runs after `func`, REGARDLESS of whether setup/func
    # passed, failed, or timed out (see run_test_case's own try/finally),
    # in its OWN separate `timeout_seconds`-sized budget rather than
    # whatever's left of the main run's budget -- a test that already used
    # its full timeout (e.g. it genuinely timed out) must not leave
    # teardown with zero time to actually run, since a test that places
    # real blocks (see actions.place_schematic) and then fails/times out
    # would otherwise leave those blocks behind for the NEXT test to trip
    # over. None means nothing to clean up (not every test places
    # anything -- e.g. `!goto`'s own test only teleports, nothing to
    # restore afterward).
    teardown: TestFunc | None = None


@dataclass
class TestOutcome:
    __test__ = False  # not a pytest test class -- see TestContext's own comment

    name: str
    passed: bool
    detail: str
    duration_seconds: float


async def run_test_case(ctx: TestContext, test: TestCase) -> None:
    """Runs `test.setup` (if any) then `test.func`, both against `ctx`,
    inside ONE combined `test.timeout_seconds` budget -- the shared
    sequencing both TestRunner (the !runtest path, see its own docstring)
    and the pytest integration driver (`minebot` repo's
    `tests/integration/`, which calls this directly rather than going
    through TestRunner at all) use, so setup/teardown-around-a-fixed-
    origin behaves identically from either entry point (see minebot-mod's
    TESTING.md "Two entry points" section for why both exist and must
    stay behaviorally identical).

    `test.teardown` (if any) then ALWAYS runs afterward, in its own
    separate `test.timeout_seconds`-sized budget -- regardless of whether
    setup/func passed, failed, or timed out (see TestCase.teardown's own
    docstring for why this must not share the main run's budget: a test
    that already burned its whole timeout failing must not leave teardown
    with nothing left to actually clean up placed blocks with). If BOTH
    the main run and teardown raise, the main run's exception is what
    propagates -- that's the one that actually describes what the test was
    testing; a teardown-only failure is logged instead of hiding it,
    though still surfaces on its own if the main run passed.

    Raises whatever setup/func themselves raise (typically
    asyncio.TimeoutError, from actions.py's own self-timing-out
    primitives, or a plain AssertionError/RuntimeError) -- TestRunner
    catches it (see _run_one below), a pytest test lets it fail the test
    normally.
    """
    async def _run() -> None:
        if test.setup is not None:
            await test.setup(ctx)
        await test.func(ctx)

    try:
        await asyncio.wait_for(_run(), timeout=test.timeout_seconds)
    except Exception:
        if test.teardown is not None:
            try:
                await asyncio.wait_for(test.teardown(ctx), timeout=test.timeout_seconds)
            except Exception:
                log.exception("!runtest: teardown for %s also failed (original failure below takes precedence)", test.name)
        raise
    else:
        if test.teardown is not None:
            await asyncio.wait_for(test.teardown(ctx), timeout=test.timeout_seconds)


class TestRegistry:
    __test__ = False  # not a pytest test class -- see TestContext's own comment

    def __init__(self) -> None:
        self._tests: dict[str, TestCase] = {}

    def register(self, test: TestCase) -> None:
        self._tests[test.name] = test

    def get(self, name: str) -> TestCase | None:
        return self._tests.get(name)

    def list_tests(self) -> list[TestCase]:
        return list(self._tests.values())


class TestRunner:
    __test__ = False  # not a pytest test class -- see TestContext's own comment

    def __init__(self, registry: TestRegistry, ctx: TestContext) -> None:
        self._registry = registry
        self._ctx = ctx

    async def run(self, name: str | None, stop_on_first_failure: bool = True) -> list[TestOutcome]:
        """Runs one named test, or every registered test (in registration
        order) if `name` is None -- matching !runtest's own no-argument
        "run all" contract. Tests run sequentially, not concurrently: they
        all drive the SAME single bot/connection (there's only one), so
        two tests running at once would just fight over its goals the same
        way two humans typing conflicting chat commands would.

        `stop_on_first_failure` (default True, per explicit direction)
        only applies to the "run all" path -- a single named test has
        nothing after it to stop before anyway. Once a test fails, every
        later test in registration order shares the SAME bot/world state
        the failed one left behind (a stuck LegsState, leftover placed
        blocks if its own teardown also failed, ...), so letting the
        whole suite barrel on typically just produces a cascade of
        unrelated-looking failures that all trace back to the first real
        one -- confirmed live, repeatedly, during this test harness's own
        development (a single genuinely stuck test derailing 3-4 later
        ones in the same run, each looking like its own separate bug
        until the FIRST one's own root cause was found). Stopping there
        surfaces the one failure that actually matters immediately,
        instead of burning the rest of the run's own time on downstream
        noise. Pass False to run the full suite regardless (e.g. to see
        the total pass/fail count across everything in one pass).
        """
        if name is not None:
            test = self._registry.get(name)
            if test is None:
                return [TestOutcome(name=name, passed=False, detail=f"no such test: {name}", duration_seconds=0.0)]
            return [await self._run_one(test)]

        outcomes: list[TestOutcome] = []
        for test in self._registry.list_tests():
            outcome = await self._run_one(test)
            outcomes.append(outcome)
            if not outcome.passed and stop_on_first_failure:
                break
        return outcomes

    async def _run_one(self, test: TestCase) -> TestOutcome:
        log.info("!runtest: starting %s", test.name)
        start = time.monotonic()
        try:
            await run_test_case(self._ctx, test)
            duration = time.monotonic() - start
            log.info("!runtest: %s PASSED (%.1fs)", test.name, duration)
            return TestOutcome(name=test.name, passed=True, detail="ok", duration_seconds=duration)
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            detail = f"timed out after {test.timeout_seconds:.0f}s"
            log.warning("!runtest: %s FAILED (%.1fs) -- %s", test.name, duration, detail)
            return TestOutcome(name=test.name, passed=False, detail=detail, duration_seconds=duration)
        except Exception as exc:
            duration = time.monotonic() - start
            log.warning("!runtest: %s FAILED (%.1fs) -- %s", test.name, duration, exc)
            return TestOutcome(name=test.name, passed=False, detail=str(exc), duration_seconds=duration)


def format_outcomes(outcomes: list[TestOutcome]) -> str:
    if len(outcomes) == 1 and outcomes[0].detail.startswith("no such test"):
        return outcomes[0].detail

    passed = sum(1 for o in outcomes if o.passed)
    lines = [f"{'PASS' if o.passed else 'FAIL'} {o.name} ({o.duration_seconds:.1f}s)" + ("" if o.passed else f": {o.detail}") for o in outcomes]
    summary = f"{passed}/{len(outcomes)} passed"
    return summary + " -- " + "; ".join(lines)
