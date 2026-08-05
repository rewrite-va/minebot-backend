"""Tracks the bot's own live position, fed from the mod's `position`
events (broadcast every client tick -- see minebot-mod's
broadcastPositionEvent). Also carries the bot's own account name
(own_name), used by run_loop.py to recognize and ignore the bot's own
chat messages (see FINDINGS.md's "chat self-echo loop" section) --
without that, a command's own reply gets heard as a fresh chat message
the same as anyone else's.
"""

from __future__ import annotations

from dataclasses import dataclass

from minebot.bridge.client import ModEvent


@dataclass
class SelfPosition:
    x: float
    y: float
    z: float
    yaw: float
    pitch: float


class SelfPositionTracker:
    def __init__(self) -> None:
        self._position: SelfPosition | None = None
        # Every position event carries the account's own name (see
        # broadcastPositionEvent) -- kept separately from `_position` so it
        # stays known even if some future position event ever omitted it,
        # rather than being bundled inside the dataclass that gets fully
        # replaced on every update.
        self._own_name: str | None = None

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "position":
            return
        self._position = SelfPosition(
            x=event.data.get("x", 0.0),
            y=event.data.get("y", 0.0),
            z=event.data.get("z", 0.0),
            yaw=event.data.get("yaw", 0.0),
            pitch=event.data.get("pitch", 0.0),
        )
        name = event.data.get("name")
        if name is not None:
            self._own_name = name

    @property
    def current(self) -> SelfPosition | None:
        return self._position

    @property
    def own_name(self) -> str | None:
        return self._own_name
