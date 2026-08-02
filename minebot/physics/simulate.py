"""Real per-tick player-movement physics, ported from
prismarine-physics@1.5.2's index.js -- the actual engine mineflayer-pathfinder
relies on for correct movement execution (its own physics.js just feeds
`bot.physics.simulatePlayer(state, world)` real per-tick control inputs).

Why this exists (see FINDINGS.md for the full trace): MovementController's
first pathfinding integration executed a planned path by interpolating
(x, z) in a straight line while independently ramping y under a
hand-rolled gravity approximation. That works for walking on flat ground,
but for any move that changes (x, z) and y at once (a diagonal step down a
ledge, walking off an edge), it can report our claimed position as
sitting horizontally *inside* the block being stepped off of, before
vertical collision has actually been resolved -- clipping through
geometry that was never really vacated. The real server rejected every
such move and reset our position every tick, live-tested and confirmed.
Real Minecraft (and mineflayer) never blends axes like that: collision is
resolved per-axis via swept AABB intersection tests against the real world
every single tick, which is what this module does.

Scope (deliberately narrower than the original): walking, falling,
jumping, step-height climbing, and ladders. Skipped: water/lava movement,
bubble columns, soul sand/honey block speed modifiers, cobwebs, sneaking
edge-detection, sprint-jump horizontal boost, potion effects, depth
strider. None of these matter for a bot that doesn't yet swim, fight, or
use items -- add them if a real use case needs them rather than porting
unused branches speculatively.

Real block collision shapes (not just solid/air) come from
block_registry.py's `shapes` field (each block state's actual
`VoxelShape.toAabbs()`, extracted the same way as the rest of the
registry) so a slab/stair correctly only blocks its actual partial box,
not the full 1x1x1 cube our earlier solid/liquid/climbable-only model
would have assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from minebot.physics.aabb import AABB
from minebot.protocol.block_registry import BLOCK_REGISTRY
from minebot.protocol.chunk_blocks import ChunkBlockCache

GRAVITY = 0.08  # blocks/tick^2
AIR_DRAG = 1.0 - 0.02  # applied to vertical velocity every tick after gravity
STEP_HEIGHT = 0.6  # max height climbable without jumping
PLAYER_HALF_WIDTH = 0.3
PLAYER_HEIGHT = 1.8
NEGLIGIBLE_VELOCITY = 0.003
AIRBORNE_INERTIA = 0.91
AIRBORNE_ACCELERATION = 0.02
DEFAULT_SLIPPERINESS = 0.6
PLAYER_SPEED = 0.1
SPRINT_SPEED = 0.3
JUMP_VELOCITY = 0.42  # Math.fround(0.42) in the JS source; float32 rounding not replicated (negligible for our purposes)
LADDER_MAX_SPEED = 0.15
LADDER_CLIMB_SPEED = 0.2
AUTOJUMP_COOLDOWN_TICKS = 10


@dataclass
class ControlState:
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False
    jump: bool = False
    sprint: bool = False


@dataclass
class PlayerPhysicsState:
    """Mutable simulation state for one entity across ticks -- mirrors
    prismarine-physics's PlayerState, minus everything tied to a live
    mineflayer bot object (inventory enchantments, status effects,
    per-version feature flags) that this scoped-down port doesn't use.
    """

    x: float
    y: float
    z: float
    yaw: float  # radians, vanilla convention (0 faces south/+z)
    vel_x: float = 0.0
    vel_y: float = 0.0
    vel_z: float = 0.0
    on_ground: bool = False
    is_collided_horizontally: bool = False
    is_collided_vertically: bool = False
    jump_ticks: int = 0
    control: ControlState = field(default_factory=ControlState)


def _get_player_bb(x: float, y: float, z: float) -> AABB:
    w = PLAYER_HALF_WIDTH
    return AABB(x - w, y, z - w, x + w, y + PLAYER_HEIGHT, z + w)


def _get_surrounding_bbs(blocks: ChunkBlockCache, query_bb: AABB) -> list[AABB]:
    """Real per-block collision boxes (not full cubes) for every block
    whose column/row/height range overlaps query_bb -- the actual
    world-collision data moveEntity sweeps against. Unknown blocks (chunk
    not loaded) are treated as having no collision at all, same
    conservative-but-necessary tradeoff as the rest of this codebase's
    unknown-terrain handling: we have no better fallback, and the
    alternative (treating unknown as solid) would make the bot refuse to
    move at the edge of loaded terrain entirely.
    """
    surrounding = []
    min_x, max_x = math.floor(query_bb.min_x), math.floor(query_bb.max_x)
    min_y, max_y = math.floor(query_bb.min_y) - 1, math.floor(query_bb.max_y)
    min_z, max_z = math.floor(query_bb.min_z), math.floor(query_bb.max_z)

    for block_y in range(min_y, max_y + 1):
        for block_z in range(min_z, max_z + 1):
            for block_x in range(min_x, max_x + 1):
                state_id = blocks.block_state_at(block_x, block_z, block_y)
                if state_id is None:
                    continue
                info = BLOCK_REGISTRY.get(state_id)
                if info is None:
                    continue
                for shape in info.shapes:
                    shape_min_x, shape_min_y, shape_min_z, shape_max_x, shape_max_y, shape_max_z = shape
                    surrounding.append(
                        AABB(
                            shape_min_x + block_x, shape_min_y + block_y, shape_min_z + block_z,
                            shape_max_x + block_x, shape_max_y + block_y, shape_max_z + block_z,
                        )
                    )
    return surrounding


def _is_on_ladder(blocks: ChunkBlockCache, x: float, y: float, z: float) -> bool:
    state_id = blocks.block_state_at(math.floor(x), math.floor(z), math.floor(y))
    return state_id is not None and BLOCK_REGISTRY.is_ladder(state_id)


def _move_entity(state: PlayerPhysicsState, blocks: ChunkBlockCache, dx: float, dy: float, dz: float) -> None:
    """Ported from moveEntity: sweeps the player's bounding box through
    (dx, dy, dz), resolving collisions per axis (y first, then x, then z --
    matching the original's order), then retries the horizontal component
    at a raised height (STEP_HEIGHT) if the direct sweep was blocked and
    the entity was on the ground or landing, picking whichever of the two
    attempts (direct vs. stepped) makes more net horizontal progress.
    """
    old_vel_x, old_vel_y, old_vel_z = dx, dy, dz

    player_bb = _get_player_bb(state.x, state.y, state.z)
    query_bb = player_bb.clone().extend(dx, dy, dz)
    surrounding = _get_surrounding_bbs(blocks, query_bb)
    old_bb = player_bb.clone()

    for block_bb in surrounding:
        dy = block_bb.compute_offset_y(player_bb, dy)
    player_bb.offset(0, dy, 0)

    for block_bb in surrounding:
        dx = block_bb.compute_offset_x(player_bb, dx)
    player_bb.offset(dx, 0, 0)

    for block_bb in surrounding:
        dz = block_bb.compute_offset_z(player_bb, dz)
    player_bb.offset(0, 0, dz)

    if STEP_HEIGHT > 0 and (state.on_ground or (dy != old_vel_y and old_vel_y < 0)) and (dx != old_vel_x or dz != old_vel_z):
        old_vel_x_col, old_vel_y_col, old_vel_z_col = dx, dy, dz
        old_bb_col = player_bb.clone()

        step_dy = STEP_HEIGHT
        stepped_query_bb = old_bb.clone().extend(old_vel_x, step_dy, old_vel_z)
        stepped_surrounding = _get_surrounding_bbs(blocks, stepped_query_bb)

        bb1 = old_bb.clone()
        bb2 = old_bb.clone()
        bb_xz = bb1.clone().extend(dx, 0, dz)

        dy1, dy2 = step_dy, step_dy
        for block_bb in stepped_surrounding:
            dy1 = block_bb.compute_offset_y(bb_xz, dy1)
            dy2 = block_bb.compute_offset_y(bb2, dy2)
        bb1.offset(0, dy1, 0)
        bb2.offset(0, dy2, 0)

        dx1, dx2 = old_vel_x, old_vel_x
        for block_bb in stepped_surrounding:
            dx1 = block_bb.compute_offset_x(bb1, dx1)
            dx2 = block_bb.compute_offset_x(bb2, dx2)
        bb1.offset(dx1, 0, 0)
        bb2.offset(dx2, 0, 0)

        dz1, dz2 = old_vel_z, old_vel_z
        for block_bb in stepped_surrounding:
            dz1 = block_bb.compute_offset_z(bb1, dz1)
            dz2 = block_bb.compute_offset_z(bb2, dz2)
        bb1.offset(0, 0, dz1)
        bb2.offset(0, 0, dz2)

        norm1 = dx1 * dx1 + dz1 * dz1
        norm2 = dx2 * dx2 + dz2 * dz2

        if norm1 > norm2:
            dx, dy, dz, player_bb = dx1, -dy1, dz1, bb1
        else:
            dx, dy, dz, player_bb = dx2, -dy2, dz2, bb2

        for block_bb in stepped_surrounding:
            dy = block_bb.compute_offset_y(player_bb, dy)
        player_bb.offset(0, dy, 0)

        if old_vel_x_col * old_vel_x_col + old_vel_z_col * old_vel_z_col >= dx * dx + dz * dz:
            dx, dy, dz, player_bb = old_vel_x_col, old_vel_y_col, old_vel_z_col, old_bb_col

    state.x = player_bb.min_x + PLAYER_HALF_WIDTH
    state.y = player_bb.min_y
    state.z = player_bb.min_z + PLAYER_HALF_WIDTH

    state.is_collided_horizontally = dx != old_vel_x or dz != old_vel_z
    state.is_collided_vertically = dy != old_vel_y
    state.on_ground = state.is_collided_vertically and old_vel_y < 0

    if dx != old_vel_x:
        state.vel_x = 0.0
    if dz != old_vel_z:
        state.vel_z = 0.0
    if dy != old_vel_y:
        state.vel_y = 0.0


def _apply_heading(state: PlayerPhysicsState, strafe: float, forward: float, multiplier: float) -> None:
    speed = math.hypot(strafe, forward)
    if speed < 0.01:
        return
    speed = multiplier / max(speed, 1.0)
    strafe *= speed
    forward *= speed

    yaw = math.pi - state.yaw
    sin_yaw = math.sin(yaw)
    cos_yaw = math.cos(yaw)
    state.vel_x += strafe * cos_yaw - forward * sin_yaw
    state.vel_z += forward * cos_yaw + strafe * sin_yaw


def _move_entity_with_heading(state: PlayerPhysicsState, blocks: ChunkBlockCache, strafe: float, forward: float) -> None:
    acceleration = AIRBORNE_ACCELERATION
    inertia = AIRBORNE_INERTIA

    if state.on_ground:
        block_under_id = blocks.block_state_at(math.floor(state.x), math.floor(state.z), math.floor(state.y) - 1)
        block_under_known = block_under_id is not None
        if block_under_known:
            # Real vanilla varies inertia/acceleration by the block's own
            # slipperiness (ice, slime, ...) -- not tracked in our
            # registry yet, so every non-ladder block under the player
            # uses the ordinary default slipperiness. Fine for now: this
            # only matters for ice/slime physics, not basic walking.
            slipperiness = DEFAULT_SLIPPERINESS
            inertia = slipperiness * 0.91
            speed_attribute = PLAYER_SPEED + (SPRINT_SPEED if state.control.sprint else 0.0)
            acceleration = speed_attribute * (0.1627714 / (inertia ** 3))
            acceleration = max(acceleration, 0.0)

    _apply_heading(state, strafe, forward, acceleration)

    on_ladder = _is_on_ladder(blocks, state.x, state.y, state.z)
    if on_ladder:
        state.vel_x = max(-LADDER_MAX_SPEED, min(state.vel_x, LADDER_MAX_SPEED))
        state.vel_z = max(-LADDER_MAX_SPEED, min(state.vel_z, LADDER_MAX_SPEED))
        state.vel_y = max(state.vel_y, -LADDER_MAX_SPEED)

    _move_entity(state, blocks, state.vel_x, state.vel_y, state.vel_z)

    if on_ladder and state.is_collided_horizontally:
        state.vel_y = LADDER_CLIMB_SPEED

    state.vel_y -= GRAVITY
    state.vel_y *= AIR_DRAG
    state.vel_x *= inertia
    state.vel_z *= inertia


def simulate_tick(state: PlayerPhysicsState, blocks: ChunkBlockCache) -> None:
    """Advances `state` by exactly one real 20Hz game tick -- the same
    granularity a real client simulates at and the server's own movement
    validation expects, confirmed necessary via live testing (see module
    docstring). Callers wanting to move over a longer span (e.g. one
    follow-loop tick) should call this once per real game tick it spans.
    """
    if state.control.jump:
        if state.jump_ticks > 0:
            state.jump_ticks -= 1
        elif state.on_ground and state.jump_ticks == 0:
            state.vel_y = JUMP_VELOCITY
            if state.control.sprint:
                yaw = math.pi - state.yaw
                state.vel_x -= math.sin(yaw) * 0.2
                state.vel_z += math.cos(yaw) * 0.2
            state.jump_ticks = AUTOJUMP_COOLDOWN_TICKS
    else:
        state.jump_ticks = 0

    if abs(state.vel_x) < NEGLIGIBLE_VELOCITY:
        state.vel_x = 0.0
    if abs(state.vel_y) < NEGLIGIBLE_VELOCITY:
        state.vel_y = 0.0
    if abs(state.vel_z) < NEGLIGIBLE_VELOCITY:
        state.vel_z = 0.0

    strafe = (float(state.control.right) - float(state.control.left)) * 0.98
    forward = (float(state.control.forward) - float(state.control.back)) * 0.98

    _move_entity_with_heading(state, blocks, strafe, forward)
