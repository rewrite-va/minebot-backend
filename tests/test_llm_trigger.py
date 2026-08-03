from minebot.llm.trigger import should_trigger_llm


def test_triggers_on_bot_name_mention():
    assert should_trigger_llm("hey minebot, got food?", "Alex", "minebot") is True


def test_triggers_case_insensitively():
    assert should_trigger_llm("Hey MineBot!", "Alex", "minebot") is True


def test_does_not_trigger_on_unrelated_chat():
    assert should_trigger_llm("anyone want to trade diamonds?", "Alex", "minebot") is False


def test_does_not_trigger_on_system_messages_with_no_sender():
    assert should_trigger_llm("minebot joined the game", None, "minebot") is False
