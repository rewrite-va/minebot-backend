"""Tracks other players' id/name/position from the mod's `entity` events
(see minebot-mod's MinebotMod.broadcastEntityEvents) -- the mod already
diffs its own live entity list every client tick, so this is just a
client-side mirror of that state, not something recomputed from raw
packets the way the old protocol-based EntityTracker had to.
"""

from __future__ import annotations

from dataclasses import dataclass

from minebot.bridge.client import ModEvent


@dataclass
class TrackedEntity:
    id: int
    name: str | None
    x: float
    y: float
    z: float


class EntityTracker:
    def __init__(self) -> None:
        self._by_id: dict[int, TrackedEntity] = {}
        self._name_to_id: dict[str, int] = {}

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "entity":
            return

        action = event.data.get("action")
        entity_id = event.data.get("id")
        if entity_id is None:
            return

        if action == "remove":
            existing = self._by_id.pop(entity_id, None)
            if existing is not None and existing.name is not None:
                self._name_to_id.pop(existing.name, None)
            return

        name = event.data.get("name")
        x = event.data.get("x")
        y = event.data.get("y")
        z = event.data.get("z")

        existing = self._by_id.get(entity_id)
        if existing is not None and name is None:
            name = existing.name
        if existing is not None and x is None:
            x, y, z = existing.x, existing.y, existing.z

        entity = TrackedEntity(id=entity_id, name=name, x=x, y=y, z=z)
        self._by_id[entity_id] = entity
        if name is not None:
            self._name_to_id[name] = entity_id

    def find_by_id(self, entity_id: int) -> TrackedEntity | None:
        return self._by_id.get(entity_id)

    def find_by_name(self, name: str) -> TrackedEntity | None:
        entity_id = self._name_to_id.get(name)
        return self._by_id.get(entity_id) if entity_id is not None else None

    def all(self) -> list[TrackedEntity]:
        return list(self._by_id.values())
