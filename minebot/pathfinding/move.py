"""A single A* graph node: one whole-block position the bot could stand at.

Direct port of mineflayer-pathfinder@2.4.5's lib/move.js. `cost` is the
*edge* cost of the move that produced this node (added to the parent's `g`
by the search, mirroring astar.js), not a total path cost by itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Move:
    x: int
    y: int
    z: int
    cost: float
    # No toBreak/toPlace/parkour bookkeeping the way move.js has -- this
    # port has no digging/placing moves at all (see movements.py), and
    # "parkour" here is just a cost-model detail, not something a caller
    # needs to branch on.

    def __post_init__(self) -> None:
        self.x = math.floor(self.x)
        self.y = math.floor(self.y)
        self.z = math.floor(self.z)

    @property
    def hash(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)
