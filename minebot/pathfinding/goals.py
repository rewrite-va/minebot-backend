"""Ported from mineflayer-pathfinder@2.4.5's lib/goals.js -- only the goal
kinds minebot's !follow command actually needs (GoalFollow, and GoalNear
which it builds on). The other goal kinds (GoalBlock, GoalXZ, GoalGetToBlock,
GoalPlaceBlock, GoalLookAtBlock, composites, ...) aren't used by anything in
minebot yet; port them when something needs them rather than speculatively.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def distance_xz(dx: float, dz: float) -> float:
    """Octile distance: diagonal moves (cost sqrt(2)) are cheaper per unit
    progress than two cardinal moves (cost 2), so the heuristic must
    account for that to stay admissible once movements.py's diagonal moves
    exist.
    """
    dx = abs(dx)
    dz = abs(dz)
    return abs(dx - dz) + min(dx, dz) * math.sqrt(2)


class Goal:
    def heuristic(self, node) -> float:
        return 0.0

    def is_end(self, node) -> bool:
        return True

    def has_changed(self) -> bool:
        return False

    def is_valid(self) -> bool:
        return True


@dataclass
class GoalNear(Goal):
    """A block position the bot should get within `range` blocks of."""

    x: float
    y: float
    z: float
    range: float

    def __post_init__(self) -> None:
        self.x = math.floor(self.x)
        self.y = math.floor(self.y)
        self.z = math.floor(self.z)
        self.range_sq = self.range * self.range

    def heuristic(self, node) -> float:
        dx = self.x - node.x
        dy = self.y - node.y
        dz = self.z - node.z
        return distance_xz(dx, dz) + abs(dy)

    def is_end(self, node) -> bool:
        dx = self.x - node.x
        dy = self.y - node.y
        dz = self.z - node.z
        return (dx * dx + dy * dy + dz * dz) <= self.range_sq


@dataclass
class GoalFollow(Goal):
    """Follows a moving target's position, re-triggering a search whenever
    the target has moved far enough for the current path to no longer be
    good (`has_changed`). `get_position` is called fresh each time instead
    of holding a live entity reference, since minebot's EntityTracker
    already owns the actual position state -- this just needs (x, y, z) at
    the moment it's asked.
    """

    get_position: object  # Callable[[], tuple[float, float, float] | None]
    range: float

    def __post_init__(self) -> None:
        position = self.get_position()
        if position is None:
            raise ValueError("GoalFollow requires a currently-known target position")
        self.x, self.y, self.z = (math.floor(v) for v in position)
        self.range_sq = self.range * self.range

    def heuristic(self, node) -> float:
        dx = self.x - node.x
        dy = self.y - node.y
        dz = self.z - node.z
        return distance_xz(dx, dz) + abs(dy)

    def is_end(self, node) -> bool:
        dx = self.x - node.x
        dy = self.y - node.y
        dz = self.z - node.z
        return (dx * dx + dy * dy + dz * dz) <= self.range_sq

    def has_changed(self) -> bool:
        position = self.get_position()
        if position is None:
            return False
        px, py, pz = (math.floor(v) for v in position)
        dx = self.x - px
        dy = self.y - py
        dz = self.z - pz
        if (dx * dx + dy * dy + dz * dz) > self.range_sq:
            self.x, self.y, self.z = px, py, pz
            return True
        return False

    def is_valid(self) -> bool:
        return self.get_position() is not None
