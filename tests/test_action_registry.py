import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult


@pytest.mark.asyncio
async def test_dispatch_chat_calls_registered_handler_positionally():
    calls = []

    async def handler(sender, distance):
        calls.append((sender, distance))
        return ActionResult()

    registry = ActionRegistry()
    registry.register(Action(
        name="forward", description="", handler=handler,
        params=[ActionParam("distance", "int", "")],
    ))

    result = await registry.dispatch_chat("!forward(3)", "Alex")

    assert result == ActionResult()
    assert calls == [("Alex", 3)]


@pytest.mark.asyncio
async def test_dispatch_chat_returns_none_for_unknown_command():
    registry = ActionRegistry()
    result = await registry.dispatch_chat("!unknownThing(1)", "Alex")
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_chat_returns_none_for_non_command_text():
    registry = ActionRegistry()
    result = await registry.dispatch_chat("just chatting, no commands here", "Alex")
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_chat_returns_handler_result():
    async def handler(sender, item, count):
        return ActionResult(message=f"gave {count}x {item}")

    registry = ActionRegistry()
    registry.register(Action(
        name="give", description="", handler=handler,
        params=[ActionParam("item", "string", ""), ActionParam("count", "int", "")],
    ))

    result = await registry.dispatch_chat('!give("stick", 1)', "Alex")

    assert result == ActionResult(message="gave 1x stick")


@pytest.mark.asyncio
async def test_dispatch_chat_truncates_extra_bare_args_beyond_the_declared_params():
    # Regression test: "!follow asd awdawd" (the bare space-separated
    # grammar has no natural stopping point) used to crash with a
    # TypeError (too many positional args) whenever a handler declared
    # fewer params than the player happened to type -- found live.
    # Trailing extra words are now silently dropped instead, since a
    # human typing extra words almost certainly didn't mean them as a
    # second argument.
    calls = []

    async def handler(sender, player_name):
        calls.append((sender, player_name))
        return ActionResult(message=f"ok, following {player_name}")

    registry = ActionRegistry()
    registry.register(Action(
        name="follow", description="", handler=handler,
        params=[ActionParam("player_name", "string", "", required=False)],
    ))

    result = await registry.dispatch_chat("!follow asd awdawd", "Alex")

    assert calls == [("Alex", "asd")]
    assert result == ActionResult(message="ok, following asd")


@pytest.mark.asyncio
async def test_dispatch_chat_swallows_handler_exceptions():
    # Regression test: a bug in one action handler must not propagate out
    # of dispatch_chat() and kill the caller's event loop (bot/run_loop.py
    # reads every subsequent event from the mod's control channel -- an
    # uncaught exception here would silently stop the bot responding to
    # anything). Matches the old CommandRegistry's same guarantee.
    async def broken_handler(sender):
        raise RuntimeError("boom")

    registry = ActionRegistry()
    registry.register(Action(name="broken", description="", handler=broken_handler))

    result = await registry.dispatch_chat("!broken", "Alex")

    assert result is not None  # the command was recognized and did run
    assert result.message is not None


@pytest.mark.asyncio
async def test_dispatch_chat_continues_working_after_a_handler_exception():
    calls = []

    async def broken_handler(sender):
        raise RuntimeError("boom")

    async def working_handler(sender):
        calls.append("ok")
        return ActionResult()

    registry = ActionRegistry()
    registry.register(Action(name="broken", description="", handler=broken_handler))
    registry.register(Action(name="fine", description="", handler=working_handler))

    await registry.dispatch_chat("!broken", "Alex")
    await registry.dispatch_chat("!fine", "Alex")

    assert calls == ["ok"]


@pytest.mark.asyncio
async def test_dispatch_tool_call_uses_keyword_args():
    calls = []

    async def handler(sender, item, count=1):
        calls.append((sender, item, count))
        return ActionResult()

    registry = ActionRegistry()
    registry.register(Action(name="give", description="", handler=handler))

    await registry.dispatch_tool_call("give", "Alex", item="bread", count=3)

    assert calls == [("Alex", "bread", 3)]


@pytest.mark.asyncio
async def test_dispatch_tool_call_unknown_action_returns_message():
    registry = ActionRegistry()
    result = await registry.dispatch_tool_call("nope", "Alex")
    assert result.message == "no such action: nope"


@pytest.mark.asyncio
async def test_dispatch_tool_call_swallows_handler_exceptions():
    async def broken_handler(sender):
        raise RuntimeError("boom")

    registry = ActionRegistry()
    registry.register(Action(name="broken", description="", handler=broken_handler))

    result = await registry.dispatch_tool_call("broken", "Alex")

    assert result.message is not None


def test_list_actions_returns_everything_registered():
    registry = ActionRegistry()
    action_a = Action(name="a", description="", handler=lambda sender: None)
    action_b = Action(name="b", description="", handler=lambda sender: None)
    registry.register(action_a)
    registry.register(action_b)

    assert registry.list_actions() == [action_a, action_b]


def test_get_returns_none_for_unknown_action():
    registry = ActionRegistry()
    assert registry.get("nope") is None
