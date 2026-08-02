"""Axis-aligned bounding box with swept-collision offset resolution --
direct port of prismarine-physics@1.5.2's lib/aabb.js, which real
Minecraft-movement-execution (mineflayer's own move.js/physics.js) relies
on for every collision check. `compute_offset_{x,y,z}` answer "how far can
`other` actually move by `offset` along this axis before hitting `self`,"
clamping the proposed offset down if `self` is in the way -- the core
primitive `moveEntity` (minebot/physics/simulate.py) sweeps per axis with.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AABB:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def clone(self) -> "AABB":
        return AABB(self.min_x, self.min_y, self.min_z, self.max_x, self.max_y, self.max_z)

    def extend(self, dx: float, dy: float, dz: float) -> "AABB":
        if dx < 0:
            self.min_x += dx
        else:
            self.max_x += dx
        if dy < 0:
            self.min_y += dy
        else:
            self.max_y += dy
        if dz < 0:
            self.min_z += dz
        else:
            self.max_z += dz
        return self

    def contract(self, x: float, y: float, z: float) -> "AABB":
        self.min_x += x
        self.min_y += y
        self.min_z += z
        self.max_x -= x
        self.max_y -= y
        self.max_z -= z
        return self

    def expand(self, x: float, y: float, z: float) -> "AABB":
        self.min_x -= x
        self.min_y -= y
        self.min_z -= z
        self.max_x += x
        self.max_y += y
        self.max_z += z
        return self

    def offset(self, x: float, y: float, z: float) -> "AABB":
        self.min_x += x
        self.min_y += y
        self.min_z += z
        self.max_x += x
        self.max_y += y
        self.max_z += z
        return self

    def compute_offset_x(self, other: "AABB", offset_x: float) -> float:
        if other.max_y > self.min_y and other.min_y < self.max_y and other.max_z > self.min_z and other.min_z < self.max_z:
            if offset_x > 0.0 and other.max_x <= self.min_x:
                offset_x = min(self.min_x - other.max_x, offset_x)
            elif offset_x < 0.0 and other.min_x >= self.max_x:
                offset_x = max(self.max_x - other.min_x, offset_x)
        return offset_x

    def compute_offset_y(self, other: "AABB", offset_y: float) -> float:
        if other.max_x > self.min_x and other.min_x < self.max_x and other.max_z > self.min_z and other.min_z < self.max_z:
            if offset_y > 0.0 and other.max_y <= self.min_y:
                offset_y = min(self.min_y - other.max_y, offset_y)
            elif offset_y < 0.0 and other.min_y >= self.max_y:
                offset_y = max(self.max_y - other.min_y, offset_y)
        return offset_y

    def compute_offset_z(self, other: "AABB", offset_z: float) -> float:
        if other.max_x > self.min_x and other.min_x < self.max_x and other.max_y > self.min_y and other.min_y < self.max_y:
            if offset_z > 0.0 and other.max_z <= self.min_z:
                offset_z = min(self.min_z - other.max_z, offset_z)
            elif offset_z < 0.0 and other.min_z >= self.max_z:
                offset_z = max(self.max_z - other.min_z, offset_z)
        return offset_z

    def intersects(self, other: "AABB") -> bool:
        return (
            self.min_x < other.max_x and self.max_x > other.min_x
            and self.min_y < other.max_y and self.max_y > other.min_y
            and self.min_z < other.max_z and self.max_z > other.min_z
        )
