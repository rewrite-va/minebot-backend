from minebot.config import BotConfig, _parse_trigger_words


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("MINEBOT_MOD_HOST", raising=False)
    monkeypatch.delenv("MINEBOT_MOD_PORT", raising=False)
    monkeypatch.delenv("MINEBOT_BOT_NAME", raising=False)
    monkeypatch.delenv("MINEBOT_TRIGGER_WORDS", raising=False)
    monkeypatch.delenv("MINEBOT_OBSERVER_HOST", raising=False)
    monkeypatch.delenv("MINEBOT_OBSERVER_PORT", raising=False)

    config = BotConfig.from_env()

    assert config.mod_host == "0.0.0.0"
    assert config.mod_port == 47893
    assert config.bot_name == "minebot"
    assert config.trigger_words == ()
    assert config.observer_host == "0.0.0.0"
    assert config.observer_port == 47894


def test_from_env_reads_trigger_words(monkeypatch):
    monkeypatch.setenv("MINEBOT_TRIGGER_WORDS", "bot, buddy , hey minebot")

    config = BotConfig.from_env()

    assert config.trigger_words == ("bot", "buddy", "hey minebot")


def test_parse_trigger_words_empty_string():
    assert _parse_trigger_words("") == ()


def test_parse_trigger_words_strips_and_drops_blanks():
    assert _parse_trigger_words("bot,, buddy ,  ") == ("bot", "buddy")
