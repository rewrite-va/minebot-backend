"""Verifies the real per-tick physics port (minebot/physics/simulate.py,
ported from prismarine-physics) against known scenarios: falling and
landing, walking on flat ground, jumping onto an obstacle too tall to step
over automatically, and refusing to walk through a solid wall.
"""

from __future__ import annotations

import pytest

from minebot.physics.simulate import GRAVITY, PlayerPhysicsState, simulate_tick
from pathfinding_fixtures import build_world, flat_ground


def test_falling_lands_exactly_on_the_floor():
    cache = build_world(flat_ground(-5, 5, -5, 5, ground_y=0))  # floor spans y=[0,1]
    state = PlayerPhysicsState(x=0.5, y=5.0, z=0.5, yaw=0.0)

    for _ in range(200):
        simulate_tick(state, cache)
        if state.on_ground:
            break
    else:
        raise AssertionError("never landed")

    assert state.y == 1.0  # feet resting exactly on the floor's top surface
    # Real vanilla (and this port, matching prismarine-physics exactly)
    # re-applies gravity every tick *after* collision resolution, so a
    # grounded entity always ends a tick with a small residual downward
    # velocity that gets clamped away by collision again next tick -- it's
    # never exactly 0 on the same tick on_ground first becomes true.
    assert state.vel_y == pytest.approx(-GRAVITY * (1 - 0.02))


def test_falling_accelerates_under_real_gravity():
    cache = build_world(flat_ground(-5, 5, -5, 5, ground_y=-100))  # floor far below -- stays airborne
    state = PlayerPhysicsState(x=0.5, y=50.0, z=0.5, yaw=0.0)

    simulate_tick(state, cache)
    first_vel = state.vel_y
    simulate_tick(state, cache)
    second_vel = state.vel_y

    # Gravity subtracted then air drag applied each tick -- velocity should
    # grow in magnitude tick over tick (not stay constant, not reverse).
    assert first_vel < 0
    assert second_vel < first_vel
    assert abs(first_vel) == pytest.approx(GRAVITY * (1 - 0.02))


def test_walking_forward_on_flat_ground_stays_on_ground():
    cache = build_world(flat_ground(-10, 10, -10, 10, ground_y=0))
    state = PlayerPhysicsState(x=0.5, y=1.0, z=0.5, yaw=0.0, on_ground=True)
    state.control.forward = True

    for _ in range(20):
        simulate_tick(state, cache)

    assert state.on_ground is True
    assert state.y == 1.0
    # Should have actually moved from the starting position.
    assert abs(state.z - 0.5) > 1.0


def test_jumping_onto_a_one_block_obstacle_reaches_the_top():
    # A step exactly 1 block taller than the floor -- too tall to
    # auto-step (STEP_HEIGHT=0.6), requires an actual jump.
    ground = flat_ground(-10, 10, -10, 10, ground_y=0)
    ground[(0, -2)] = 1  # this column's block spans y=[1,2], top surface at y=2
    cache = build_world(ground)

    state = PlayerPhysicsState(x=0.5, y=1.0, z=0.5, yaw=0.0, on_ground=True)
    state.control.forward = True
    state.control.jump = True

    reached_top = False
    for _ in range(60):
        simulate_tick(state, cache)
        if state.on_ground and state.y == 2.0:
            reached_top = True
            break

    assert reached_top


def test_walking_into_a_wall_stops_at_its_face():
    ground = flat_ground(-10, 10, -10, 10, ground_y=0)
    cache = build_world(ground, extra_solid={(0, 1, -2), (0, 2, -2)})  # a 2-tall wall, too high to step/jump onto
    state = PlayerPhysicsState(x=0.5, y=1.0, z=0.5, yaw=0.0, on_ground=True)
    state.control.forward = True
    state.control.jump = True

    for _ in range(60):
        simulate_tick(state, cache)

    # Never passes z=-2 (the wall's near face) -- may bounce/jump against
    # it repeatedly, but must not clip through.
    assert state.z > -2.0


def test_step_height_climbs_a_real_half_block_slab_without_jumping():
    # A real bottom-half slab (0.5 blocks tall) sitting directly on the
    # floor -- exactly the kind of obstacle STEP_HEIGHT (0.6) exists for:
    # climbable as ordinary walking collision, no jump input needed. This
    # is also the first real exercise of block_registry.py's actual
    # per-block collision shapes (not just a full-cube approximation).
    from minebot.protocol.block_registry import BLOCK_REGISTRY
    from minebot.protocol.chunk_blocks import SECTION_SIZE

    bottom_slab = next(
        info for info in BLOCK_REGISTRY.states_named("minecraft:oak_slab")
        if info.shapes == ((0.0, 0.0, 0.0, 1.0, 0.5, 1.0),)
    )

    ground = flat_ground(-10, 10, -10, 10, ground_y=0)
    cache = build_world(ground)
    # Place the slab one column ahead (south, -z) directly on top of the
    # floor, i.e. occupying the bottom half of the y=[1,2] block space.
    # world (x=0, z=-2) is chunk (0, -1), local (0, 14).
    chunk = cache._chunks[(0, -1)]
    slab_world_y = 1
    section_y = (slab_world_y - cache.min_y) // SECTION_SIZE
    local_y = (slab_world_y - cache.min_y) % SECTION_SIZE
    section = chunk.sections[section_y]
    section.block_state_ids[(local_y << 8) | (14 << 4) | 0] = bottom_slab.id  # local (x=0, z=14) i.e. world z=-2

    state = PlayerPhysicsState(x=0.5, y=1.0, z=0.5, yaw=0.0, on_ground=True)
    state.control.forward = True  # no jump control -- must climb via step-height alone

    reached_top = False
    for _ in range(40):
        simulate_tick(state, cache)
        if state.on_ground and state.y == pytest.approx(1.5):
            reached_top = True
            break

    assert reached_top
