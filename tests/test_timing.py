import importlib
import logging

from minebot import timing


def _reload_with_env(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("DEBUG", raising=False)
    else:
        monkeypatch.setenv("DEBUG", value)
    return importlib.reload(timing)


def test_disabled_by_default(monkeypatch):
    module = _reload_with_env(monkeypatch, None)
    assert module.enabled is False


def test_enabled_by_debug_true(monkeypatch):
    module = _reload_with_env(monkeypatch, "true")
    assert module.enabled is True


def test_enabled_by_debug_1(monkeypatch):
    module = _reload_with_env(monkeypatch, "1")
    assert module.enabled is True


def test_disabled_by_other_values(monkeypatch):
    module = _reload_with_env(monkeypatch, "false")
    assert module.enabled is False


def test_log_timing_only_logs_when_enabled(monkeypatch, caplog):
    logger = logging.getLogger("test.timing")

    module = _reload_with_env(monkeypatch, None)
    with caplog.at_level("DEBUG"):
        module.log_timing(logger, "should not appear")
    assert "should not appear" not in caplog.text

    module = _reload_with_env(monkeypatch, "true")
    with caplog.at_level("DEBUG"):
        module.log_timing(logger, "should appear")
    assert "should appear" in caplog.text

    importlib.reload(timing)  # restore module state for later tests
