"""LLMController: the brain layer triggered when a chat message is
addressed to the bot (see trigger.should_trigger_llm) and isn't itself a
!command. Structured so a real provider integration is a matter of
implementing LLMProvider and passing it in -- nothing about ActionRegistry,
the run loop, or the chat-trigger logic needs to know or care which
provider (or whether one is even configured yet).

No provider is wired up yet (see FINDINGS.md) -- NullLLMProvider is the
default, and just declines to answer, so the trigger/dispatch plumbing
above it can be built, tested, and used today without an API key.
"""

from __future__ import annotations

import logging
from typing import Protocol

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action
from minebot.bridge.client import ModBridge

log = logging.getLogger("minebot.llm")


class LLMProvider(Protocol):
    """What a real provider adapter (Anthropic/OpenAI/etc.) needs to
    implement -- takes the conversation turn plus the full action list (so
    it can build that provider's own tool-schema format) and returns
    however many tool calls the model chose to make, if any, plus its own
    chat reply text if it has one. LLMController is the thing that
    actually executes those tool calls through ActionRegistry and decides
    what (if anything) gets said back to chat -- the provider's job is
    purely "turn a conversation + action list into a response", not to
    talk to the bridge or registry directly.
    """

    async def respond(
        self, sender: str, text: str, available_actions: list[Action],
    ) -> "LLMResponse":
        ...


class LLMResponse:
    def __init__(self, reply: str | None = None, tool_calls: list["LLMToolCall"] | None = None) -> None:
        self.reply = reply
        self.tool_calls = tool_calls or []


class LLMToolCall:
    def __init__(self, action_name: str, **kwargs) -> None:
        self.action_name = action_name
        self.kwargs = kwargs


class NullLLMProvider:
    """Placeholder used until a real provider is configured -- always
    declines rather than silently pretending to think about it, so it's
    obvious in testing/logs that no model is actually wired up yet.
    """

    async def respond(self, sender: str, text: str, available_actions: list[Action]) -> LLMResponse:
        log.info("LLM trigger fired for <%s> %r, but no LLM provider is configured", sender, text)
        return LLMResponse(reply=None)


class LLMController:
    def __init__(self, bridge: ModBridge, actions: ActionRegistry, provider: LLMProvider | None = None) -> None:
        self.bridge = bridge
        self.actions = actions
        self.provider = provider or NullLLMProvider()

    async def handle_chat(self, sender: str | None, text: str) -> None:
        if sender is None:
            return

        response = await self.provider.respond(sender, text, self.actions.list_actions())

        for call in response.tool_calls:
            result = await self.actions.dispatch_tool_call(call.action_name, sender, **call.kwargs)
            if result.message:
                await self.bridge.send_chat(result.message)

        if response.reply:
            await self.bridge.send_chat(response.reply)
