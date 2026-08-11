"""!runtest [name] -- runs one registered in-game test, or every test with
no argument, against the ALREADY-CONNECTED bot -- see minebot/testing/
runner.py's own docstring for why this is a from-chat-command tier
distinct from the launch-a-fresh-client pytest tier minebot-mod's
TESTING.md describes. Built specifically so a human can join a second
Prism instance as a spectator, watch the bot, and type !runtest to kick
off a real test run live -- per explicit direction, no separate script
should be needed to fire the scripted tests themselves, only to launch a
fresh client for the unattended tier.
"""

from __future__ import annotations

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.testing.runner import TestRunner, format_outcomes


def register_testing_actions(registry: ActionRegistry, runner: TestRunner) -> None:
    async def handler(sender: str | None, name: str | None = None) -> ActionResult:
        outcomes = await runner.run(name)
        return ActionResult(message=format_outcomes(outcomes))

    registry.register(Action(
        name="runtest",
        description="Run one named in-game test, or all registered tests if no name is given.",
        handler=handler,
        params=[
            ActionParam("name", "string", "Name of the specific test to run.", required=False),
        ],
    ))
