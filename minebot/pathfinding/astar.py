"""A* search, ported from mineflayer-pathfinder@2.4.5's lib/astar.js. Same
g/h/f scoring, closed set, and open-set update semantics; uses Python's
built-in `heapq` instead of porting astar.js's own hand-rolled binary heap
(lib/heap.js) -- heapq already gives us an equivalent priority queue, so
reimplementing a custom one would just be extra code with the same
behavior.
"""

from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass
from typing import Callable

from minebot.pathfinding.move import Move


@dataclass
class PathNode:
    data: Move
    g: float = 0.0
    h: float = 0.0
    f: float = 0.0
    parent: "PathNode | None" = None

    def set(self, data: Move, g: float, h: float, parent: "PathNode | None" = None) -> "PathNode":
        self.data = data
        self.g = g
        self.h = h
        self.f = g + h
        self.parent = parent
        return self


def _reconstruct_path(node: PathNode) -> list[Move]:
    path = []
    while node.parent is not None:
        path.append(node.data)
        node = node.parent
    path.reverse()
    return path


@dataclass
class AStarResult:
    status: str  # "success" | "partial" | "timeout" | "noPath"
    cost: float
    time: float
    visited_nodes: int
    generated_nodes: int
    path: list[Move]


class AStar:
    def __init__(
        self,
        start: Move,
        get_neighbors: Callable[[Move], list[Move]],
        heuristic: Callable[[Move], float],
        is_end: Callable[[Move], bool],
        timeout: float,
        tick_timeout: float = 0.04,
        search_radius: float = -1,
    ) -> None:
        self._start_time = time.monotonic()
        self._get_neighbors = get_neighbors
        self._heuristic = heuristic
        self._is_end = is_end
        self._timeout = timeout
        self._tick_timeout = tick_timeout

        self._closed_data_set: set[tuple[int, int, int]] = set()
        self._open_heap: list[tuple[float, int, PathNode]] = []
        self._open_data_map: dict[tuple[int, int, int], PathNode] = {}
        self._counter = itertools.count()  # heapq tie-breaker, since PathNode isn't orderable

        start_node = PathNode(start, g=0.0, h=heuristic(start))
        self._push_open(start_node)
        self._open_data_map[start_node.data.hash] = start_node
        self.best_node = start_node

        self._max_cost = -1.0 if search_radius < 0 else start_node.h + search_radius

    def _push_open(self, node: PathNode) -> None:
        heapq.heappush(self._open_heap, (node.f, next(self._counter), node))

    def _pop_open(self) -> PathNode:
        while self._open_heap:
            f, _, node = heapq.heappop(self._open_heap)
            if node.data.hash not in self._open_data_map:
                continue  # stale entry from an update -- see _update_open
            if self._open_data_map[node.data.hash] is not node:
                continue  # superseded by a better node for the same position
            return node
        raise IndexError("pop from empty heap")

    def _update_open(self, node: PathNode) -> None:
        # heapq has no decrease-key: push a fresh entry and let the stale one
        # be skipped in _pop_open once popped (identity check against
        # _open_data_map, which always points at the current best node).
        self._push_open(node)

    def _make_result(self, status: str, node: PathNode) -> AStarResult:
        return AStarResult(
            status=status,
            cost=node.g,
            time=time.monotonic() - self._start_time,
            visited_nodes=len(self._closed_data_set),
            generated_nodes=len(self._closed_data_set) + len(self._open_data_map),
            path=_reconstruct_path(node),
        )

    def compute(self) -> AStarResult:
        compute_start_time = time.monotonic()
        while self._open_heap:
            if time.monotonic() - compute_start_time > self._tick_timeout:
                return self._make_result("partial", self.best_node)
            if time.monotonic() - self._start_time > self._timeout:
                return self._make_result("timeout", self.best_node)

            node = self._pop_open()
            if self._is_end(node.data):
                return self._make_result("success", node)

            del self._open_data_map[node.data.hash]
            self._closed_data_set.add(node.data.hash)

            for neighbor_data in self._get_neighbors(node.data):
                if neighbor_data.hash in self._closed_data_set:
                    continue

                g_from_this_node = node.g + neighbor_data.cost
                neighbor_node = self._open_data_map.get(neighbor_data.hash)

                heuristic = self._heuristic(neighbor_data)
                if self._max_cost > 0 and g_from_this_node + heuristic > self._max_cost:
                    continue

                if neighbor_node is None:
                    neighbor_node = PathNode(neighbor_data)
                    self._open_data_map[neighbor_data.hash] = neighbor_node
                    neighbor_node.set(neighbor_data, g_from_this_node, heuristic, node)
                    if neighbor_node.h < self.best_node.h:
                        self.best_node = neighbor_node
                    self._push_open(neighbor_node)
                else:
                    if neighbor_node.g < g_from_this_node:
                        continue  # another route is already faster
                    neighbor_node.set(neighbor_data, g_from_this_node, heuristic, node)
                    if neighbor_node.h < self.best_node.h:
                        self.best_node = neighbor_node
                    self._update_open(neighbor_node)

        return self._make_result("noPath", self.best_node)
