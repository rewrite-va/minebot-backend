"""Integration test for the head-clearance-blocked test scenario
(goto_leaves_1.litematic) -- proves the bot never reaches a target blocked
only by floating leaves overhead (a 2-block-tall hitbox clearance problem,
not a horizontal wall the way goto_impossible_1 is) AND never attempts a
real jump trying to force its way there, against a real client/world.
Runs against the single shared client this directory's conftest.py
launches once per session (see its own docstring for why one client, not
one per test).

Same "two entry points, one shared TestCase" pattern as test_goto.py/
test_goto_impossible.py -- runs the exact registered "goto_leaves_1"
TestCase (minebot/testing/tests.py), including its setup (teleport to the
schematic's own "start" marker + place_schematic) and teardown
(clear_schematic), via the shared run_test_case sequencing.
"""

from __future__ import annotations

import pytest

from minebot.testing import tests as ingame_tests
from minebot.testing.runner import TestContext, TestRegistry, run_test_case


@pytest.mark.asyncio
async def test_goto_leaves_1_integration(ctx: TestContext):
    registry = TestRegistry()
    ingame_tests.register_default_tests(registry)
    test_case = registry.get("goto_leaves_1")
    assert test_case is not None
    await run_test_case(ctx, test_case)
