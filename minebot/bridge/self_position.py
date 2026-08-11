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
from typing import Callable

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
        # Fired on EVERY real position event, in order, not just the
        # latest -- `.current` alone only ever exposes the most recent
        # sample, which is enough for goto()/teleport()'s own arrival
        # polling but not for a caller that needs to see the FULL trail
        # the bot walked (e.g. actions.goto_with_waypoints checking every
        # tick's position against yellow/red marker columns, not just
        # wherever the bot happened to be at whatever moment it last
        # polled). Listeners are added/removed for the duration of a
        # single walk (see goto_with_waypoints's own docstring), not
        # meant to accumulate across a whole test run.
        self._listeners: list[Callable[[SelfPosition], None]] = []

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
        for listener in list(self._listeners):
            listener(self._position)

    @property
    def current(self) -> SelfPosition | None:
        return self._position

    @property
    def own_name(self) -> str | None:
        return self._own_name

    def add_listener(self, listener: Callable[[SelfPosition], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[SelfPosition], None]) -> None:
        self._listeners.remove(listener)
