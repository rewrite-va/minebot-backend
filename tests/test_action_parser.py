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
