import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bot.help import register_help_action


def _registry_with_sample_actions() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(Action(
        name="stop", description="Stop moving.", handler=lambda sender: None,
    ))
    registry.register(Action(
        name="follow", description="Walk to and follow a player.", handler=lambda sender, player_name=None: None,
        params=[ActionParam("player_name", "string", "Name of the player to follow.", required=False)],
    ))
    register_help_action(registry)
    return registry


@pytest.mark.asyncio
async def test_help_with_no_args_lists_every_command():
    registry = _registry_with_sample_actions()
    help_action = registry.get("help")

    result = await help_action.handler(None)

    assert "!stop" in result.message
    assert "!follow" in result.message
    assert "!help" in result.message


@pytest.mark.asyncio
async def test_help_with_a_command_name_describes_it():
    registry = _registry_with_sample_actions()
    help_action = registry.get("help")

    result = await help_action.handler(None, "follow")

    assert "!follow" in result.message
    assert "Walk to and follow a player." in result.message
    assert "player_name" in result.message


@pytest.mark.asyncio
async def test_help_accepts_a_leading_bang_on_the_command_name():
    registry = _registry_with_sample_actions()
    help_action = registry.get("help")

    result = await help_action.handler(None, "!stop")

    assert "Stop moving." in result.message


@pytest.mark.asyncio
async def test_help_with_unknown_command_reports_it():
    registry = _registry_with_sample_actions()
    help_action = registry.get("help")

    result = await help_action.handler(None, "doesnotexist")

    assert result == ActionResult(message="no such command: !doesnotexist")


@pytest.mark.asyncio
async def test_help_dispatches_as_a_chat_command():
    registry = _registry_with_sample_actions()

    result = await registry.dispatch_chat("!help stop", "Alex")

    assert "Stop moving." in result.message
