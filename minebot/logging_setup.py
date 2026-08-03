"""Console + file logging setup. Every run also writes to its own
timestamped file under logs/ (gitignored), alongside the usual console
output -- console-only logging meant a live bug report could only be
diagnosed from whatever the terminal's scrollback still had, or from
manually piping to `tee` (as happened investigating the FoodEater/
death-respawn stale-jar bug) rather than something always captured by
default.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")


def configure_logging(level: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # force=True: basicConfig is a no-op if the root logger already has
    # handlers attached (e.g. pytest's own log-capture plugin installs
    # one before any test runs) -- without this, a second call in the
    # same process (any test after the first exercising this function)
    # silently kept the first call's handlers instead of reconfiguring,
    # so log records went to a stale file from a previous configuration.
    logging.basicConfig(level=level.upper(), handlers=[console_handler, file_handler], force=True)
    return log_path
