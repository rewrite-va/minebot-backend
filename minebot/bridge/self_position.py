"""Tracks the bot's own live position, fed from the mod's `position`
events (broadcast every client tick -- see minebot-mod's
broadcastPositionEvent). Nothing tracked this before now; run_loop.py
only ever logged position events at debug level and threw them away --
needed as soon as any action wants to know "where am I right now"
(e.g. !remember saving the current spot).
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

    @property
    def current(self) -> SelfPosition | None:
        return self._position
