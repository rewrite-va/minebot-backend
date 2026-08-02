"""Verifies Movements' cost model (ported from mineflayer-pathfinder's
movements.js, walk/climb/parkour only -- see pathfinding/movements.py's
docstring for what was deliberately dropped) against synthetic block
layouts built with tests/pathfinding_fixtures.py, checking real neighbor
sets and costs rather than just "doesn't crash."
"""

from __future__ import annotations

from minebot.pathfinding.move import Move
from minebot.pathfinding.movements import Movements
from pathfinding_fixtures import build_world, flat_ground


def test_flat_ground_has_eight_neighbors():
    # A 3x3 chunk block of flat floor so the center column isn't near an
    # unloaded-chunk edge (which would correctly show up as "unknown").
    cache = build_world(flat_ground(-20, 20, -20, 20, ground_y=0))
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    neighbors = movements.get_neighbors(start)
    positions = sorted((n.x, n.y, n.z) for n in neighbors)

    expected = sorted(
        (dx, 1, dz)
        for dx in (-1, 0, 1)
        for dz in (-1, 0, 1)
        if not (dx == 0 and dz == 0)
    )
    assert positions == expected


def test_cardinal_move_cost_is_one_diagonal_is_sqrt2():
    cache = build_world(flat_ground(-5, 5, -5, 5, ground_y=0))
    movements = Movements(blocks=cache)
    start = Move(0, 1, 0, 0)

    forward = movements.get_move_forward(start, 1, 0)
    diagonal = movements.get_move_diagonal(start, 1, 1)

    assert forward.cost == 1.0
    assert abs(diagonal.cost - 2 ** 0.5) < 1e-9


def test_step_up_one_block_is_available_and_capped_correctly():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    ground[(1, 0)] = 1  # one block taller directly east
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    jump_up = movements.get_move_jump_up(start, 1, 0)
    assert jump_up is not None
    assert jump_up.x == 1 and jump_up.y == 2 and jump_up.z == 0


def test_step_up_two_blocks_is_too_high():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    ground[(1, 0)] = 2  # two blocks taller -- exceeds the 1.2-block cap
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    assert movements.get_move_jump_up(start, 1, 0) is None


def test_forward_blocked_by_wall_two_blocks_tall():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    cache = build_world(ground, extra_solid={(1, 1, 0), (1, 2, 0)})
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    assert movements.get_move_forward(start, 1, 0) is None
    # Can't jump over it either -- it's 2 blocks tall from the floor.
    assert movements.get_move_jump_up(start, 1, 0) is None


def test_drop_down_into_a_pit_lands_on_the_far_floor():
    # Floor at y=0 everywhere except column (1,0), which is open all the
    # way down to a floor at y=-3 instead.
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    del ground[(1, 0)]
    cache = build_world(ground, extra_solid={(1, -3, 0)})
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    drop = movements.get_move_drop_down(start, 1, 0)
    assert drop is not None
    assert drop.x == 1 and drop.y == -2 and drop.z == 0  # standing on top of the y=-3 floor


def test_drop_down_returns_none_when_no_floor_within_search_range():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    del ground[(1, 0)]  # bottomless (within the fixture's loaded chunk) pit
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    assert movements.get_move_drop_down(start, 1, 0) is None


def test_unknown_chunk_is_conservatively_treated_as_blocked():
    cache = build_world(flat_ground(0, 15, 0, 15, ground_y=0))  # only chunk (0,0)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    # (-1, 0) is in an unloaded neighboring chunk.
    assert movements.get_move_forward(start, -1, 0) is None


def test_get_move_up_requires_ladder_not_just_any_move():
    cache = build_world(flat_ground(-5, 5, -5, 5, ground_y=0))
    movements = Movements(blocks=cache)
    start = Move(0, 1, 0, 0)

    assert movements.get_move_up(start) is None  # no ladder -- can't 1x1 tower without placing


def test_liquid_column_still_walkable_but_costs_extra():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    cache = build_world(ground)

    from minebot.protocol.block_registry import BLOCK_REGISTRY
    water_states = BLOCK_REGISTRY.states_named("minecraft:water")
    assert water_states
    water_state_id = water_states[0].id

    min_y = -64
    section_y = (1 - min_y) // 16
    local_y = (1 - min_y) % 16
    chunk = cache._chunks[(0, 0)]
    section = chunk.sections[section_y]
    section.block_state_ids[(local_y << 8) | (0 << 4) | 1] = water_state_id

    movements = Movements(blocks=cache)
    start = Move(0, 1, 0, 0)
    forward = movements.get_move_forward(start, 1, 0)
    assert forward is not None
    assert forward.cost == 1.0  # liquidCost only applies to the bot's OWN current block, not the destination
