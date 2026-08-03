"""Wire/event timing instrumentation, gated by DEBUG=true -- separate from
MINEBOT_LOG_LEVEL, which controls ordinary log verbosity (every position/
entity event, full message bodies, ...) and is too noisy to leave on just
to watch send/receive/process timestamps. Added to debug a real deadlock
where a find_result reply sat fully received on the wire for 10 seconds
before anything read it (see FINDINGS.md's "The find_result deadlock") --
kept afterward since the same instrumentation is the fastest way to
confirm or rule out a similar timing/ordering issue in the future.
"""

from __future__ import annotations

import logging
import os
import time

enabled = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


def log_timing(logger: logging.Logger, message: str, *args: object) -> None:
    if enabled:
        logger.debug(message, *args)


def now() -> float:
    return time.monotonic()
