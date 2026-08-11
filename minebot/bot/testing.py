"""!runtest [name] [stop_on_first_failure] -- runs one registered in-game
test, or every test with no name given, against the ALREADY-CONNECTED bot
-- see minebot/testing/runner.py's own docstring for why this is a
from-chat-command tier distinct from the launch-a-fresh-client pytest
tier minebot-mod's TESTING.md describes. Built specifically so a human
can join a second Prism instance as a spectator, watch the bot, and type
!runtest to kick off a real test run live -- per explicit direction, no
separate script should be needed to fire the scripted tests themselves,
only to launch a fresh client for the unattended tier.

"Run all" (no name given) stops at the first failure by default -- see
TestRunner.run's own docstring for why: a failed test's leftover state
(a stuck LegsState, leftover placed blocks) tends to cascade into
unrelated-looking failures in every test after it, burning the rest of
the run's time on downstream noise instead of surfacing the one failure
that actually matters. `!runtest all false` (name="all", the literal
run-everything default, plus stop_on_first_failure=false) runs the whole
suite regardless, e.g. to see the total pass/fail count in one pass.
"""

from __future__ import annotations

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.testing.runner import TestRunner, format_outcomes


def register_testing_actions(registry: ActionRegistry, runner: TestRunner) -> None:
    async def handler(sender: str | None, name: str | None = None, stop_on_first_failure: bool = True) -> ActionResult:
        # "all" is not a real registered test name -- it's the explicit
        # way to ask for "run every test" while ALSO passing
        # stop_on_first_failure, since the chat grammar has no way to
        # supply a second positional arg while leaving the first one
        # unset. TestRunner.run's own "name is None" contract still means
        # "run every test" for every other caller (e.g. main.py's own
        # startup path never needs this override at all).
        run_name = None if name is None or name == "all" else name
        outcomes = await runner.run(run_name, stop_on_first_failure=stop_on_first_failure)
        return ActionResult(message=format_outcomes(outcomes))

    registry.register(Action(
        name="runtest",
        description="Run one named in-game test, or all registered tests if no name is given (stops at the first failure by default -- pass 'all false' to run every test regardless).",
        handler=handler,
        params=[
            ActionParam("name", "string", "Name of a specific test to run, or 'all' to run every test.", required=False),
            ActionParam("stop_on_first_failure", "bool", "Stop the 'run all' path at the first failure (default true).", required=False),
        ],
    ))
