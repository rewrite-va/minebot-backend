"""Buffers per-tick `replay_frame` events (see minebot-mod's own
ReplayFrameBuilder, emitted only when the connected client was launched
with -Dminebot.recordReplay=true) for the duration of ONE test run, plus
whatever blocks that test's own setup placed via actions.place_schematic,
then writes the whole thing as one JSON file for minebot-frontend's replay
viewer to load. Test-scoped (start()/stop() bracket exactly one
run_test_case call, see runner.py) rather than a persistent always-on
tracker -- buffering unconditionally for the life of the process would grow
unbounded whenever recordReplay is left on across many tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minebot.bridge.client import ModEvent

log = logging.getLogger("minebot.testing.replay")

_REPLAY_DIR_ENV = "MINEBOT_REPLAY_DIR"

# Sibling-repo layout, same assumption BotConfig's own mod_repo_path default
# already makes -- minebot and minebot-frontend are checked out next to
# each other.
_DEFAULT_REPLAY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "minebot-frontend" / "replays"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class ReplayRecorder:
    __test__ = False  # not a pytest test class -- see runner.TestContext's own comment

    _frames: list[dict[str, Any]] = field(default_factory=list)
    _placed_blocks: list[tuple[int, int, int, int, int, int, str]] = field(default_factory=list)
    # Real-world-offset (x, y, z, role) points -- role is one of
    # litematic.WAYPOINT_BLOCK_ROLES' own values ("start"/"path"/
    # "forbidden"/"end"/"unreachable"), i.e. exactly what each wool color
    # in the source schematic meant. Kept separate from _placed_blocks
    # (which records the raw /fill runs a schematic places, including the
    # wool blocks themselves) since a viewer wants to tell "this is scenery
    # the bot walks on" apart from "this is an assertion marker" -- see
    # goto_with_waypoints' own call site in actions.py for where these are
    # captured, already offset the same way placed_blocks now is.
    _waypoints: list[tuple[float, float, float, str]] = field(default_factory=list)
    _recording: bool = False

    def start(self) -> None:
        self._frames = []
        self._placed_blocks = []
        self._waypoints = []
        self._recording = True

    def stop(self) -> list[dict[str, Any]]:
        self._recording = False
        frames = self._frames
        self._frames = []
        return frames

    @property
    def placed_blocks(self) -> list[tuple[int, int, int, int, int, int, str]]:
        return self._placed_blocks

    @property
    def waypoints(self) -> list[tuple[float, float, float, str]]:
        return self._waypoints

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "replay_frame" or not self._recording:
            return
        self._frames.append(event.data)

    def record_blocks(self, runs: list[tuple[int, int, int, int, int, int, str]]) -> None:
        self._placed_blocks.extend(runs)

    def record_waypoints(self, waypoints: list[tuple[float, float, float, str]]) -> None:
        self._waypoints.extend(waypoints)


def replay_output_dir() -> Path:
    override = os.environ.get(_REPLAY_DIR_ENV)
    return Path(override) if override else _DEFAULT_REPLAY_DIR


def _safe_filename_part(value: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", value)


def write_replay(
    test_name: str,
    commit: str | None,
    started_at: datetime,
    passed: bool,
    detail: str,
    duration_seconds: float,
    frames: list[dict[str, Any]],
    placed_blocks: list[tuple[int, int, int, int, int, int, str]],
    waypoints: list[tuple[float, float, float, str]] = (),
) -> Path:
    """Writes <test_name>_<commit>_<datetime>.json into replay_output_dir()
    and returns the path written. `commit` is expected to come from
    mod_version.expected_commit (the mod repo's own current git commit),
    not the mod's self-reported `hello` commit -- see runner.run_test_case's
    own call site for why.
    """
    out_dir = replay_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    commit_part = _safe_filename_part((commit or "unknown")[:12])
    datetime_part = started_at.strftime("%Y%m%dT%H%M%S")
    filename = f"{_safe_filename_part(test_name)}_{commit_part}_{datetime_part}.json"

    payload = {
        "metadata": {
            "test_name": test_name,
            "commit": commit,
            "started_at": started_at.isoformat(),
            "passed": passed,
            "detail": detail,
            "duration_seconds": duration_seconds,
        },
        "placed_blocks": [
            {"x1": x1, "y": y1, "z1": z1, "x2": x2, "z2": z2, "block": block}
            for x1, y1, z1, x2, y2, z2, block in placed_blocks
        ],
        "waypoints": [
            {"x": x, "y": y, "z": z, "role": role}
            for x, y, z, role in waypoints
        ],
        "frames": frames,
    }

    out_path = out_dir / filename
    out_path.write_text(json.dumps(payload))
    log.info("wrote replay for %s to %s", test_name, out_path)
    return out_path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
