"""Integration test for the schematic-placement test scenario -- proves
place_schematic/clear_schematic actually work against a real client/world,
not just that _fill_runs' pure merging logic is correct (see
tests/test_testing_actions.py for that unit coverage). Runs against the
single shared client this directory's conftest.py launches once per
session (see its own docstring for why one client, not one per test).

Same "two entry points, one shared TestCase" pattern as test_goto.py --
runs the exact registered "goto_onto_schematic" TestCase (minebot/testing/
tests.py), including its setup (teleport + place_schematic) and teardown
(clear_schematic), via the shared run_test_case sequencing. Using the
registered TestCase, not the bare test function, matters here specifically
because it's what runs setup/teardown too -- calling
test_goto_arrives_on_schematic_block directly would skip both the
schematic placement AND its cleanup, silently testing nothing and leaving
blocks behind for whatever runs next.
"""

from __future__ import annotations

import pytest

from minebot.testing import tests as ingame_tests
from minebot.testing.runner import TestContext, TestRegistry, run_test_case


@pytest.mark.asyncio
async def test_goto_onto_schematic_integration(ctx: TestContext):
    registry = TestRegistry()
    ingame_tests.register_default_tests(registry)
    test_case = registry.get("goto_onto_schematic")
    assert test_case is not None
    await run_test_case(ctx, test_case)
