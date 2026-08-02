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
