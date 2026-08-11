"""Integration test for !goto -- the actual A-to-B pathfinding assertion
minebot-mod's TESTING.md set out to build as its "first slice". Runs
against the single shared client this directory's conftest.py launches
once per session (see its own docstring for why one client, not one per
test).

Runs the exact same registered TestCase !runtest's own "goto" test runs
manually (minebot/testing/tests.py's "goto" TestCase, including its
setup_goto teleport-to-a-fixed-origin step) via the shared run_test_case
sequencing (see runner.py's own docstring) -- this file is the
automated/unattended entry point into it, never chat/!runtest itself
(see minebot-mod's TESTING.md "Two entry points" section). Using the
registered TestCase (not calling test_goto_moves_bot_to_target directly)
matters here specifically because it's what runs `setup` too -- calling
the bare test function alone would skip the fixed-origin teleport and
silently go back to testing from whatever position the bot happens to be
left at.
"""

from __future__ import annotations

import pytest

from minebot.testing import tests as ingame_tests
from minebot.testing.runner import TestContext, TestRegistry, run_test_case


@pytest.mark.asyncio
async def test_goto_moves_bot_to_target_integration(ctx: TestContext):
    registry = TestRegistry()
    ingame_tests.register_default_tests(registry)
    test_case = registry.get("goto")
    assert test_case is not None
    await run_test_case(ctx, test_case)
