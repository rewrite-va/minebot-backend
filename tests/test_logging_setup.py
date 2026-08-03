import logging

from minebot.logging_setup import LOG_DIR, configure_logging


def test_configure_logging_creates_a_timestamped_file_under_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    log_path = configure_logging("INFO")

    assert log_path.parent == LOG_DIR
    assert log_path.exists()
    assert (tmp_path / "logs").is_dir()

    # Clean up handlers so later tests in the same process don't keep
    # writing to this test's temp file.
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


def test_configure_logging_writes_records_to_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    log_path = configure_logging("INFO")
    logging.getLogger("test").info("hello from the test")
    for handler in logging.root.handlers:
        handler.flush()

    assert "hello from the test" in log_path.read_text()

    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)
