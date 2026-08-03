from minebot.actions.parser import parse_command


def test_parses_bare_command():
    parsed = parse_command("!forward")
    assert parsed is not None
    assert parsed.name == "forward"
    assert parsed.args == []


def test_parses_numeric_args():
    parsed = parse_command("!goToCoordinates(100, 64, -200, 2)")
    assert parsed.name == "goToCoordinates"
    assert parsed.args == [100, 64, -200, 2]


def test_parses_float_args():
    parsed = parse_command("!forward(1.5)")
    assert parsed.args == [1.5]


def test_parses_string_and_bool_args():
    parsed = parse_command('!equip("diamond_sword", true)')
    assert parsed.args == ["diamond_sword", True]


def test_ignores_surrounding_chat_text():
    parsed = parse_command("hey bot can you do !forward(2) please")
    assert parsed.name == "forward"
    assert parsed.args == [2]


def test_returns_none_when_no_command():
    assert parse_command("just chatting, no commands here") is None


def test_parses_a_single_bare_word_arg():
    # Regression test: "!follow yayaeue" used to silently parse as
    # zero-arg !follow (only the parenthesized form was recognized),
    # so the name given was discarded entirely and follow fell back to
    # the chat sender instead -- found live.
    parsed = parse_command("!follow yayaeue")
    assert parsed.name == "follow"
    assert parsed.args == ["yayaeue"]


def test_parses_multiple_bare_word_args():
    parsed = parse_command("!give riterite bread 5")
    assert parsed.name == "give"
    assert parsed.args == ["riterite", "bread", 5]


def test_bare_args_stop_at_the_next_command():
    parsed = parse_command("!follow yayaeue !stop")
    assert parsed.name == "follow"
    assert parsed.args == ["yayaeue"]


def test_bare_arg_parsing_coerces_numbers_and_booleans():
    parsed = parse_command("!forward 2.5 true")
    assert parsed.args == [2.5, True]


def test_parenthesized_form_still_takes_priority_over_bare_form():
    parsed = parse_command('!equip("diamond_sword")')
    assert parsed.args == ["diamond_sword"]
