from minebot.llm.trigger import should_trigger_llm


def test_triggers_on_bot_name_mention():
    assert should_trigger_llm("hey minebot, got food?", "Alex", "minebot") is True


def test_triggers_case_insensitively():
    assert should_trigger_llm("Hey MineBot!", "Alex", "minebot") is True


def test_does_not_trigger_on_unrelated_chat():
    assert should_trigger_llm("anyone want to trade diamonds?", "Alex", "minebot") is False


def test_does_not_trigger_on_system_messages_with_no_sender():
    assert should_trigger_llm("minebot joined the game", None, "minebot") is False


def test_triggers_on_a_configured_trigger_word():
    assert should_trigger_llm("hey bot, got food?", "Alex", "minebot", ["bot", "buddy"]) is True


def test_triggers_on_a_configured_trigger_word_case_insensitively():
    assert should_trigger_llm("Hey BUDDY!", "Alex", "minebot", ["bot", "buddy"]) is True


def test_does_not_trigger_when_no_name_or_trigger_word_present():
    assert should_trigger_llm("anyone want to trade diamonds?", "Alex", "minebot", ["bot", "buddy"]) is False


def test_trigger_words_default_to_empty():
    assert should_trigger_llm("hey bot, got food?", "Alex", "minebot") is False


def test_blank_trigger_words_are_ignored():
    assert should_trigger_llm("hello there", "Alex", "minebot", ["", "  "]) is False
