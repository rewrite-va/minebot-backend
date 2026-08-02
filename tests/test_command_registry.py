import pytest

from minebot.commands.registry import CommandRegistry


@pytest.mark.asyncio
async def test_dispatch_calls_registered_handler():
    calls = []

    async def handler(distance):
        calls.append(distance)

    registry = CommandRegistry()
    registry.register("forward", handler)

    dispatched = await registry.dispatch("!forward(3)")

    assert dispatched is True
    assert calls == [3]


@pytest.mark.asyncio
async def test_dispatch_returns_false_for_unknown_command():
    registry = CommandRegistry()
    dispatched = await registry.dispatch("!unknownThing(1)")
    assert dispatched is False


@pytest.mark.asyncio
async def test_dispatch_passes_context_args_before_parsed_args():
    calls = []

    async def handler(bot, item, count):
        calls.append((bot, item, count))

    registry = CommandRegistry()
    registry.register("give", handler)

    await registry.dispatch('!give("stick", 1)', "bot-context")

    assert calls == [("bot-context", "stick", 1)]


@pytest.mark.asyncio
async def test_dispatch_swallows_handler_exceptions():
    # Regression test: a bug in one command handler must not propagate out
    # of dispatch() and kill the caller's read loop (run_play_loop reads
    # every subsequent packet, including keepalives -- an uncaught
    # exception here would silently stop the bot responding to anything).
    async def broken_handler():
        raise RuntimeError("boom")

    registry = CommandRegistry()
    registry.register("broken", broken_handler)

    dispatched = await registry.dispatch("!broken")

    assert dispatched is True  # the command was recognized and did run


@pytest.mark.asyncio
async def test_dispatch_continues_working_after_a_handler_exception():
    calls = []

    async def broken_handler():
        raise RuntimeError("boom")

    async def working_handler():
        calls.append("ok")

    registry = CommandRegistry()
    registry.register("broken", broken_handler)
    registry.register("fine", working_handler)

    await registry.dispatch("!broken")
    await registry.dispatch("!fine")

    assert calls == ["ok"]
