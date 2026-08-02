from __future__ import annotations

import pytest

from minebot.pathfinding.goals import GoalFollow, GoalNear
from minebot.pathfinding.move import Move


def test_goal_near_is_end_within_range():
    goal = GoalNear(10, 5, 10, range=2.0)
    assert goal.is_end(Move(10, 5, 10, 0))
    assert goal.is_end(Move(11, 5, 10, 0))
    assert not goal.is_end(Move(20, 5, 10, 0))


def test_goal_near_heuristic_is_zero_at_target():
    goal = GoalNear(10, 5, 10, range=2.0)
    assert goal.heuristic(Move(10, 5, 10, 0)) == 0.0


def test_goal_near_floors_fractional_coordinates():
    goal = GoalNear(10.9, 5.1, 10.9, range=1.0)
    assert goal.x == 10
    assert goal.y == 5
    assert goal.z == 10


def test_goal_follow_tracks_live_position():
    position = [10.0, 5.0, 10.0]
    goal = GoalFollow(get_position=lambda: tuple(position), range=2.0)

    assert goal.is_end(Move(10, 5, 10, 0))
    assert not goal.is_end(Move(100, 5, 10, 0))


def test_goal_follow_has_changed_when_target_moves_far():
    position = [0.0, 0.0, 0.0]
    goal = GoalFollow(get_position=lambda: tuple(position), range=2.0)

    assert not goal.has_changed()  # target hasn't moved yet

    position[0] = 50.0
    assert goal.has_changed()
    assert goal.x == 50


def test_goal_follow_has_changed_false_for_small_movement_within_range():
    position = [0.0, 0.0, 0.0]
    goal = GoalFollow(get_position=lambda: tuple(position), range=2.0)

    position[0] = 1.0  # within range, shouldn't invalidate the current path
    assert not goal.has_changed()


def test_goal_follow_is_valid_false_when_position_unavailable():
    goal = GoalFollow(get_position=lambda: (0.0, 0.0, 0.0), range=2.0)
    goal.get_position = lambda: None
    assert not goal.is_valid()


def test_goal_follow_raises_if_no_initial_position():
    with pytest.raises(ValueError):
        GoalFollow(get_position=lambda: None, range=2.0)
