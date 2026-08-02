"""Cost model for A*, ported from mineflayer-pathfinder@2.4.5's
lib/movements.js -- but walk/climb/parkour moves only. Every digging and
block-placing branch from the original is dropped: minebot has no
digging or block-placement packets implemented at all yet, so those moves
could never actually be executed even if the search found a path using
them (mineflayer's own `canDig = false` setting disables the same branches
for the same reason). Entity-avoidance cost weighting and scaffolding-item
tracking are also dropped -- they need bot.entities/inventory data this
port doesn't have a use for yet. Get real digging/placing support first if
those are wanted; this is deliberately the "can only walk, climb, and jump
across small gaps over terrain that's already open" subset.

Block classification (`safe`/`physical`/`liquid`/`climbable`) mirrors
movements.js's `getBlock` using our own block_registry.py-derived
solid/liquid/ladder flags instead of prismarine-block/minecraft-data.
Every block query that lands in a chunk we haven't received yet is treated
as unsafe/non-physical (i.e. "don't route through it") rather than
optimistically assumed open or solid -- unlike mineflayer, which always
has full world data once a chunk is loaded, we have no fallback source for
unknown terrain, so guessing wrong in either direction could either walk
the bot off a ledge or make it think open space is a wall. Erring toward
"don't go there" is the safe default for a pathfinder call whose whole
point is not falling through unseen gaps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from minebot.pathfinding.move import Move
from minebot.protocol.block_registry import BLOCK_REGISTRY
from minebot.protocol.chunk_blocks import ChunkBlockCache

_CARDINAL_DIRECTIONS = [
    (-1, 0),  # West
    (1, 0),  # East
    (0, -1),  # North
    (0, 1),  # South
]
_DIAGONAL_DIRECTIONS = [
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
]

_BLOCKED = 100.0  # movements.js's "can't move here" cost sentinel
_MAX_STEP_HEIGHT = 1.2  # movements.js's hardcoded jump-height cap (blocks)


@dataclass
class BlockInfo:
    x: int
    y: int
    z: int
    known: bool  # False if the containing chunk hasn't been received yet
    safe: bool  # empty/climbable/carpet-like and not something to avoid
    physical: bool  # a solid full block you can stand on
    liquid: bool
    climbable: bool

    @property
    def height(self) -> float:
        return self.y + (1.0 if self.physical else 0.0)


@dataclass
class Movements:
    blocks: ChunkBlockCache
    allow_parkour: bool = True
    allow_sprinting: bool = True

    def get_block(self, origin, dx: int, dy: int, dz: int) -> BlockInfo:
        """`origin` is anything with .x/.y/.z integer coordinates -- a
        `Move` (a graph node being expanded) or a `BlockInfo` (chaining a
        lookup relative to a block already fetched, e.g. walking a
        landing search down column by column).
        """
        x, y, z = origin.x + dx, origin.y + dy, origin.z + dz
        state_id = self.blocks.block_state_at(x, z, y)

        if state_id is None:
            return BlockInfo(x=x, y=y, z=z, known=False, safe=False, physical=False, liquid=False, climbable=False)

        info = BLOCK_REGISTRY.get(state_id)
        is_air = info is None or info.air
        is_liquid = info is not None and info.liquid
        is_ladder = info is not None and info.ladder
        is_solid = info is not None and info.solid

        # movements.js: b.safe = (boundingBox === 'empty' || climbable || carpet) && !avoid.
        # Liquids have no collision box in vanilla (you can swim through
        # them), so they count as "empty"/safe too -- confirmed against
        # getLandingBlock's `blockLand.liquid && blockLand.safe` check,
        # which would be dead code if liquid could never also be safe. No
        # "carpet"/avoid-block classification yet -- see module docstring.
        safe = is_air or is_ladder or is_liquid
        return BlockInfo(x=x, y=y, z=z, known=True, safe=safe, physical=is_solid, liquid=is_liquid, climbable=is_ladder)

    def _safe_or_blocked(self, block: BlockInfo) -> float:
        """movements.js's safeOrBreak, minus every branch that computes a
        digging cost (we can never actually dig): a safe block costs
        nothing extra, anything else is impassable (_BLOCKED) since we
        have no way to clear it.
        """
        if not block.known:
            return _BLOCKED
        if block.safe:
            return 0.0
        return _BLOCKED

    def get_move_forward(self, node: Move, dx: int, dz: int) -> Move | None:
        block_b = self.get_block(node, dx, 1, dz)
        block_c = self.get_block(node, dx, 0, dz)
        block_d = self.get_block(node, dx, -1, dz)

        cost = 1.0

        if not block_d.physical and not block_c.liquid:
            return None  # would need to place a block to fill the gap -- can't dig/place

        cost += self._safe_or_blocked(block_b)
        if cost > _BLOCKED:
            return None

        cost += self._safe_or_blocked(block_c)
        if cost > _BLOCKED:
            return None

        if self.get_block(node, 0, 0, 0).liquid:
            cost += 1.0  # liquidCost

        return Move(block_c.x, block_c.y, block_c.z, cost)

    def get_move_jump_up(self, node: Move, dx: int, dz: int) -> Move | None:
        block_a = self.get_block(node, 0, 2, 0)
        block_h = self.get_block(node, dx, 2, dz)
        block_b = self.get_block(node, dx, 1, dz)
        block_c = self.get_block(node, dx, 0, dz)

        cost = 2.0  # move + jump

        if not block_c.physical:
            return None  # would need to place a block to stand on -- can't place

        block_0 = self.get_block(node, 0, -1, 0)
        if block_c.height - block_0.height > _MAX_STEP_HEIGHT:
            return None  # too high to jump

        cost += self._safe_or_blocked(block_a)
        if cost > _BLOCKED:
            return None
        cost += self._safe_or_blocked(block_h)
        if cost > _BLOCKED:
            return None
        cost += self._safe_or_blocked(block_b)
        if cost > _BLOCKED:
            return None

        return Move(block_b.x, block_b.y, block_b.z, cost)

    def get_move_diagonal(self, node: Move, dx: int, dz: int) -> Move | None:
        cost = math.sqrt(2)

        block_c = self.get_block(node, dx, 0, dz)  # landing block, or the block we'd stand on if stepping up
        y = 1 if block_c.physical else 0

        block_0 = self.get_block(node, 0, -1, 0)

        cost1 = 0.0
        block_b1 = self.get_block(node, 0, y + 1, dz)
        block_c1 = self.get_block(node, 0, y, dz)
        block_d1 = self.get_block(node, 0, y - 1, dz)
        cost1 += self._safe_or_blocked(block_b1)
        cost1 += self._safe_or_blocked(block_c1)
        if block_d1.height - block_0.height > _MAX_STEP_HEIGHT:
            cost1 += self._safe_or_blocked(block_d1)

        cost2 = 0.0
        block_b2 = self.get_block(node, dx, y + 1, 0)
        block_c2 = self.get_block(node, dx, y, 0)
        block_d2 = self.get_block(node, dx, y - 1, 0)
        cost2 += self._safe_or_blocked(block_b2)
        cost2 += self._safe_or_blocked(block_c2)
        if block_d2.height - block_0.height > _MAX_STEP_HEIGHT:
            cost2 += self._safe_or_blocked(block_d2)

        cost += min(cost1, cost2)
        if cost > _BLOCKED:
            return None

        cost += self._safe_or_blocked(self.get_block(node, dx, y, dz))
        if cost > _BLOCKED:
            return None
        cost += self._safe_or_blocked(self.get_block(node, dx, y + 1, dz))
        if cost > _BLOCKED:
            return None

        if self.get_block(node, 0, 0, 0).liquid:
            cost += 1.0  # liquidCost

        block_d = self.get_block(node, dx, -1, dz)
        if y == 1:  # stepping up by 1 while moving diagonally
            if block_c.height - block_0.height > _MAX_STEP_HEIGHT:
                return None
            cost += self._safe_or_blocked(self.get_block(node, 0, 2, 0))
            if cost > _BLOCKED:
                return None
            cost += 1.0
            return Move(block_c.x, block_c.y + 1, block_c.z, cost)
        elif block_d.physical or block_c.liquid:
            return Move(block_c.x, block_c.y, block_c.z, cost)
        elif self.get_block(node, dx, -2, dz).physical or block_d.liquid:
            if not block_d.safe:
                return None  # don't self-immolate (e.g. drop into lava)
            return Move(block_c.x, block_c.y - 1, block_c.z, cost)
        return None

    def _get_landing_block(self, node: Move, dx: int, dz: int) -> BlockInfo | None:
        block_land = self.get_block(node, dx, -2, dz)
        # No dimension min_y tracking here (see chunk_blocks.py) -- bound
        # the fall search by the deepest column we actually have block
        # data for instead, which naturally stops at the bottom of loaded
        # chunk data rather than looping forever over unknown blocks.
        for _ in range(256):
            if not block_land.known:
                return None
            if block_land.liquid and block_land.safe:
                return block_land
            if block_land.physical:
                return self.get_block(block_land, 0, 1, 0)
            if not block_land.safe:
                return None
            block_land = self.get_block(block_land, 0, -1, 0)
        return None

    def get_move_drop_down(self, node: Move, dx: int, dz: int) -> Move | None:
        block_b = self.get_block(node, dx, 1, dz)
        block_c = self.get_block(node, dx, 0, dz)
        block_d = self.get_block(node, dx, -1, dz)

        cost = 1.0

        block_land = self._get_landing_block(node, dx, dz)
        if block_land is None:
            return None

        cost += self._safe_or_blocked(block_b)
        if cost > _BLOCKED:
            return None
        cost += self._safe_or_blocked(block_c)
        if cost > _BLOCKED:
            return None
        cost += self._safe_or_blocked(block_d)
        if cost > _BLOCKED:
            return None

        if block_c.liquid:
            return None  # don't go underwater

        return Move(block_land.x, block_land.y, block_land.z, cost)

    def get_move_down(self, node: Move) -> Move | None:
        block_0 = self.get_block(node, 0, -1, 0)

        cost = 1.0

        block_land = self._get_landing_block(node, 0, 0)
        if block_land is None:
            return None

        cost += self._safe_or_blocked(block_0)
        if cost > _BLOCKED:
            return None

        if self.get_block(node, 0, 0, 0).liquid:
            return None  # don't go underwater

        return Move(block_land.x, block_land.y, block_land.z, cost)

    def get_move_up(self, node: Move) -> Move | None:
        block_1 = self.get_block(node, 0, 0, 0)
        if block_1.liquid:
            return None

        block_2 = self.get_block(node, 0, 2, 0)

        cost = 1.0
        cost += self._safe_or_blocked(block_2)
        if cost > _BLOCKED:
            return None

        if not block_1.climbable:
            return None  # can only climb via a ladder/vine -- no 1x1 towering, which needs placing

        return Move(node.x, node.y + 1, node.z, cost)

    def get_move_parkour_forward(self, node: Move, dx: int, dz: int) -> list[Move]:
        moves: list[Move] = []

        block_0 = self.get_block(node, 0, -1, 0)
        block_1 = self.get_block(node, dx, -1, dz)
        if (block_1.physical and block_1.height >= block_0.height) or \
                not self.get_block(node, dx, 0, dz).safe or \
                not self.get_block(node, dx, 1, dz).safe:
            return moves
        if self.get_block(node, 0, 0, 0).liquid:
            return moves  # can't jump from water

        cost = 1.0

        ceiling_clear = self.get_block(node, 0, 2, 0).safe and self.get_block(node, dx, 2, dz).safe
        floor_cleared = not self.get_block(node, dx, -2, dz).physical

        max_d = 4 if self.allow_sprinting else 2

        for d in range(2, max_d + 1):
            ddx = dx * d
            ddz = dz * d
            block_a = self.get_block(node, ddx, 2, ddz)
            block_b = self.get_block(node, ddx, 1, ddz)
            block_c = self.get_block(node, ddx, 0, ddz)
            block_d = self.get_block(node, ddx, -1, ddz)

            if ceiling_clear and block_b.safe and block_c.safe and block_d.physical:
                moves.append(Move(block_c.x, block_c.y, block_c.z, cost))
                break
            elif ceiling_clear and block_b.safe and block_c.physical:
                if block_a.safe and d != 4:  # 4-forward-1-up is very difficult and fails often
                    if block_c.height - block_0.height > _MAX_STEP_HEIGHT:
                        break  # too high to jump
                    moves.append(Move(block_b.x, block_b.y, block_b.z, cost))
                    break
            elif (ceiling_clear or d == 2) and block_b.safe and block_c.safe and block_d.safe and floor_cleared:
                block_e = self.get_block(node, ddx, -2, ddz)
                if block_e.physical:
                    moves.append(Move(block_d.x, block_d.y, block_d.z, cost))
                floor_cleared = floor_cleared and not block_e.physical
            elif not block_b.safe or not block_c.safe:
                break

            ceiling_clear = ceiling_clear and block_a.safe

        return moves

    def get_neighbors(self, node: Move) -> list[Move]:
        neighbors: list[Move] = []

        for dx, dz in _CARDINAL_DIRECTIONS:
            forward = self.get_move_forward(node, dx, dz)
            if forward is not None:
                neighbors.append(forward)
            jump_up = self.get_move_jump_up(node, dx, dz)
            if jump_up is not None:
                neighbors.append(jump_up)
            drop_down = self.get_move_drop_down(node, dx, dz)
            if drop_down is not None:
                neighbors.append(drop_down)
            if self.allow_parkour:
                neighbors.extend(self.get_move_parkour_forward(node, dx, dz))

        for dx, dz in _DIAGONAL_DIRECTIONS:
            diagonal = self.get_move_diagonal(node, dx, dz)
            if diagonal is not None:
                neighbors.append(diagonal)

        down = self.get_move_down(node)
        if down is not None:
            neighbors.append(down)
        up = self.get_move_up(node)
        if up is not None:
            neighbors.append(up)

        return neighbors
