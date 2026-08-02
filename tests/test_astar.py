"""Verifies AStar's search mechanics (ported from mineflayer-pathfinder's
astar.js) in isolation, using a trivial synthetic graph rather than real
block data -- the block-aware cost model is tested separately in
test_movements.py/test_pathfinding_integration.py.
"""

from __future__ import annotations

from minebot.pathfinding.astar import AStar
from minebot.pathfinding.move import Move


def _line_graph_neighbors(node: Move) -> list[Move]:
    return [Move(node.x - 1, 0, 0, 1.0), Move(node.x + 1, 0, 0, 1.0)]


def _manhattan_to(target_x: int):
    def heuristic(node: Move) -> float:
        return abs(target_x - node.x)
    return heuristic


def _is_at(target_x: int):
    def is_end(node: Move) -> bool:
        return node.x == target_x
    return is_end


def test_finds_shortest_path_on_simple_line():
    start = Move(0, 0, 0, 0)
    astar = AStar(start, _line_graph_neighbors, _manhattan_to(5), _is_at(5), timeout=5.0)
    result = astar.compute()

    assert result.status == "success"
    assert result.cost == 5.0
    assert [m.x for m in result.path] == [1, 2, 3, 4, 5]


def test_start_already_at_goal_returns_empty_path():
    start = Move(5, 0, 0, 0)
    astar = AStar(start, _line_graph_neighbors, _manhattan_to(5), _is_at(5), timeout=5.0)
    result = astar.compute()

    assert result.status == "success"
    assert result.path == []
    assert result.cost == 0.0


def test_no_path_when_neighbors_are_empty():
    start = Move(0, 0, 0, 0)
    astar = AStar(start, lambda node: [], _manhattan_to(5), _is_at(5), timeout=5.0)
    result = astar.compute()

    assert result.status == "noPath"


def test_timeout_returns_partial_best_effort():
    start = Move(0, 0, 0, 0)
    # A target that's never reachable (heuristic always positive, no
    # neighbor is ever the end) forces the search to run until timeout.
    astar = AStar(start, _line_graph_neighbors, _manhattan_to(10**9), lambda node: False, timeout=0.01, tick_timeout=0.005)
    result = astar.compute()

    assert result.status in ("timeout", "partial")


def test_prefers_cheaper_route_when_multiple_exist():
    # A 2D grid where going straight along x costs 1/step, but a "shortcut"
    # diagonal edge directly from (0,0) to (3,0) costs less than the sum of
    # three unit steps -- the search should take it.
    def neighbors(node: Move) -> list[Move]:
        result = [Move(node.x + 1, 0, 0, 1.0)]
        if node.x == 0:
            result.append(Move(3, 0, 0, 1.5))  # shortcut cheaper than 3 unit steps
        return result

    start = Move(0, 0, 0, 0)
    astar = AStar(start, neighbors, _manhattan_to(3), _is_at(3), timeout=5.0)
    result = astar.compute()

    assert result.status == "success"
    assert result.cost == 1.5
    assert [m.x for m in result.path] == [3]
