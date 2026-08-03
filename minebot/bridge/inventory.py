"""Mirrors the bot's own inventory, fed purely from the mod's `inventory`
events (see minebot-mod's InventoryReporter) -- each event is a full
snapshot (not a diff), broadcast whenever the mod's own copy changes, so
this class just replaces its state wholesale on every event rather than
reconciling adds/removes the way EntityTracker has to for entities.
"""

from __future__ import annotations

from dataclasses import dataclass

from minebot.bridge.client import ModEvent


@dataclass
class InventorySlot:
    slot: int
    item: str
    count: int
    damage: int | None = None
    max_damage: int | None = None


class InventoryTracker:
    def __init__(self) -> None:
        self._by_slot: dict[int, InventorySlot] = {}
        self._selected_slot: int | None = None

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "inventory":
            return

        self._selected_slot = event.data.get("selected_slot")
        self._by_slot = {}
        for raw_slot in event.data.get("slots", []):
            slot = InventorySlot(
                slot=raw_slot["slot"],
                item=raw_slot["item"],
                count=raw_slot["count"],
                damage=raw_slot.get("damage"),
                max_damage=raw_slot.get("max_damage"),
            )
            self._by_slot[slot.slot] = slot

    @property
    def selected_slot(self) -> int | None:
        return self._selected_slot

    def slots(self) -> list[InventorySlot]:
        return list(self._by_slot.values())

    def slot(self, slot: int) -> InventorySlot | None:
        return self._by_slot.get(slot)

    def find_by_item(self, item: str) -> InventorySlot | None:
        """Returns the first slot holding `item` (a full registry id like
        "minecraft:bread"), or None if the bot isn't carrying any."""
        for entry in self._by_slot.values():
            if entry.item == item:
                return entry
        return None

    def count_of(self, item: str) -> int:
        return sum(entry.count for entry in self._by_slot.values() if entry.item == item)
