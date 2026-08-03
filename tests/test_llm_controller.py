import pytest

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionResult
from minebot.llm.controller import LLMController, LLMResponse, LLMToolCall, NullLLMProvider


class RecordingBridge:
    def __init__(self):
        self.sent_chat: list[str] = []

    async def send_chat(self, text: str) -> None:
        self.sent_chat.append(text)


class FakeProvider:
    def __init__(self, response: LLMResponse):
        self.response = response
        self.calls = []

    async def respond(self, sender, text, available_actions):
        self.calls.append((sender, text, available_actions))
        return self.response


@pytest.mark.asyncio
async def test_null_provider_never_replies():
    bridge = RecordingBridge()
    controller = LLMController(bridge, ActionRegistry())  # default NullLLMProvider

    await controller.handle_chat("Alex", "hey minebot")

    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_ignores_chat_with_no_sender():
    bridge = RecordingBridge()
    provider = FakeProvider(LLMResponse(reply="should not be reached"))
    controller = LLMController(bridge, ActionRegistry(), provider)

    await controller.handle_chat(None, "minebot joined the game")

    assert provider.calls == []
    assert bridge.sent_chat == []


@pytest.mark.asyncio
async def test_sends_the_providers_reply_to_chat():
    bridge = RecordingBridge()
    provider = FakeProvider(LLMResponse(reply="I'm doing great!"))
    controller = LLMController(bridge, ActionRegistry(), provider)

    await controller.handle_chat("Alex", "how are you minebot?")

    assert bridge.sent_chat == ["I'm doing great!"]


@pytest.mark.asyncio
async def test_passes_the_full_action_list_to_the_provider():
    actions = ActionRegistry()
    action = Action(name="give", description="give an item", handler=lambda sender: None)
    actions.register(action)
    bridge = RecordingBridge()
    provider = FakeProvider(LLMResponse())
    controller = LLMController(bridge, actions, provider)

    await controller.handle_chat("Alex", "hi")

    assert provider.calls[0][2] == [action]


@pytest.mark.asyncio
async def test_executes_tool_calls_through_the_action_registry():
    calls = []

    async def give_handler(sender, item):
        calls.append((sender, item))
        return ActionResult(message=f"gave {item}")

    actions = ActionRegistry()
    actions.register(Action(name="give", description="", handler=give_handler))

    bridge = RecordingBridge()
    provider = FakeProvider(LLMResponse(tool_calls=[LLMToolCall("give", item="bread")]))
    controller = LLMController(bridge, actions, provider)

    await controller.handle_chat("Alex", "can I get some bread minebot?")

    assert calls == [("Alex", "bread")]
    assert bridge.sent_chat == ["gave bread"]


@pytest.mark.asyncio
async def test_sends_both_tool_call_result_and_final_reply():
    async def give_handler(sender, item):
        return ActionResult(message=f"gave {item}")

    actions = ActionRegistry()
    actions.register(Action(name="give", description="", handler=give_handler))

    bridge = RecordingBridge()
    provider = FakeProvider(LLMResponse(reply="there you go!", tool_calls=[LLMToolCall("give", item="bread")]))
    controller = LLMController(bridge, actions, provider)

    await controller.handle_chat("Alex", "bread please")

    assert bridge.sent_chat == ["gave bread", "there you go!"]
