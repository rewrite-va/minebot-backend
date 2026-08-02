"""End-to-end A* + Movements + GoalNear tests against synthetic terrain --
this is what actually exercises the scenario reported in FINDINGS.md ("bot
tries to fall through solid ground when behind a target who drops a
level"): given real block data, does the search find a path that walks
around/over/down terrain instead of a straight line through it.
"""

from __future__ import annotations

from minebot.pathfinding.astar import AStar
from minebot.pathfinding.goals import GoalNear
from minebot.pathfinding.move import Move
from minebot.pathfinding.movements import Movements
from pathfinding_fixtures import build_world, flat_ground


def _run(start: Move, goal: GoalNear, movements: Movements):
    astar = AStar(start, movements.get_neighbors, goal.heuristic, goal.is_end, timeout=5.0)
    return astar.compute()


def test_paths_around_a_pit_instead_of_falling_through_it():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    del ground[(1, 0)]  # a gap directly on the straight-line path
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    goal = GoalNear(3, 1, 0, range=0.5)
    result = _run(start, goal, movements)

    assert result.status == "success"
    visited_xz = {(m.x, m.z) for m in result.path}
    assert (1, 0) not in visited_xz  # never steps into the gap


def test_paths_down_a_staircase_the_bot_is_behind_on():
    # This is the exact reported scenario: the target has dropped down a
    # level and the bot, still behind, must step down a staircase rather
    # than trying to fall straight through solid ground at its own column.
    ground = flat_ground(-5, 5, -5, 5, ground_y=3)
    ground[(1, 0)] = 2
    ground[(2, 0)] = 1
    ground[(3, 0)] = 0
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 4, 0, 0)  # standing on the ground_y=3 floor
    goal = GoalNear(3, 1, 0, range=0.5)  # target now down at the ground_y=0 floor
    result = _run(start, goal, movements)

    assert result.status == "success"
    path_ys = [m.y for m in result.path]
    # Monotonically non-increasing Y all the way down the stairs -- no
    # jump back up, no getting stuck.
    assert path_ys == sorted(path_ys, reverse=True)
    assert path_ys[-1] == 1  # standing on the final (ground_y=0) step


def test_climbs_a_single_step_directly_toward_the_goal():
    ground = flat_ground(-5, 5, -5, 5, ground_y=0)
    ground[(1, 0)] = 1
    ground[(2, 0)] = 1
    cache = build_world(ground)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    goal = GoalNear(2, 2, 0, range=0.5)
    result = _run(start, goal, movements)

    assert result.status == "success"
    assert result.path[-1].y == 2


def test_no_path_when_fully_walled_in():
    ground = flat_ground(-2, 2, -2, 2, ground_y=0)
    # A solid wall of blocks surrounding the start on all four sides, two
    # blocks tall (too tall to step or jump over) and with no way around
    # within the loaded fixture area (world beyond it is unknown/unloaded).
    wall = set()
    for d in range(-1, 2):
        for h in (1, 2):
            wall.add((1, h, d))
            wall.add((-1, h, d))
            wall.add((d, h, 1))
            wall.add((d, h, -1))
    cache = build_world(ground, extra_solid=wall)
    movements = Movements(blocks=cache)

    start = Move(0, 1, 0, 0)
    goal = GoalNear(2, 1, 2, range=0.5)
    result = _run(start, goal, movements)

    assert result.status == "noPath"
