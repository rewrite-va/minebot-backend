"""Mirrors the bot's own inventory, fed purely from the mod's `inventory`
events (see minebot-mod's InventoryReporter) -- each event is a full
snapshot (not a diff), broadcast whenever the mod's own copy changes, so
this class just replaces its state wholesale on every event rather than
reconciling adds/removes the way EntityTracker has to for entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from minebot.bridge.client import ModEvent

log = logging.getLogger("minebot.inventory_tracker")


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
        # !give ("drop back whatever was just grabbed") needs to know the
        # most recently *gained* item -- there's no dedicated pickup event
        # anywhere (checked: no Fabric API hook exists for it, since real
        # pickups happen server-side and the client only ever observes the
        # resulting inventory change, indistinguishable at that point from
        # crafting/trading/being given something).
        #
        # Two pointers, not an eagerly-recomputed single field: `_previous`
        # is the per-item-total snapshot from before the most recent
        # `inventory` event, `_current` from the most recent one. Whatever
        # changed between them is computed on demand (last_gained_item),
        # comparing exactly these two snapshots -- not "whichever item
        # increased the most" (that heuristic broke live: the bot already
        # carried a large stack of golden carrots, was given a single
        # phantom membrane, and "biggest increase" mistakenly attributed
        # the pre-existing carrots -- present in both snapshots, so a real
        # per-item diff shows zero change for them -- as the latest gain).
        # `_previous`/`_current` both start empty -- the very first
        # `inventory` event this tracker ever sees has nothing genuine to
        # diff against, so everything in it reads as "gained" that one
        # time (accepted as unavoidable: there's no way to know what the
        # bot carried before the mod started reporting). The mod now
        # sends one snapshot immediately on connect (see
        # InventoryReporter.forceNextBroadcast) specifically so this
        # baseline gets established right away instead of waiting for the
        # first real change after connecting.
        self._previous: dict[str, int] = {}
        self._current: dict[str, int] = {}
        # Plain callback list, not an asyncio.Event -- deliberately
        # decoupled from any single consumer's own waiting mechanism.
        # !collect's "did the count of item X actually go up" wait
        # (MiningController) and the independent "announce any real gain
        # to chat" observer (see run_loop.py's on_inventory_change) are
        # two unrelated consumers of the same underlying signal ("the
        # inventory just changed, gained_items() is worth checking
        # again"), registered here rather than either one owning the
        # other. Called synchronously, right after _current/_previous
        # are updated, so every callback always sees fully up-to-date
        # state by the time it runs.
        self._on_change: list[Callable[[], None]] = []

    def add_change_listener(self, callback: Callable[[], None]) -> None:
        self._on_change.append(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        """Callers that add a short-lived listener (e.g. MiningController's
        per-collect-attempt wait) must remove it once they're done waiting,
        or the list grows unbounded across a long-running process -- this
        is a plain list.remove, so removing something never added (or
        already removed) would raise; every caller is expected to pair
        add/remove in a try/finally around its own wait, same shape as
        any other resource-cleanup pattern in this codebase.
        """
        self._on_change.remove(callback)

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

        self._previous = self._current
        self._current = self._totals_by_item()
        # gained_items()'s 2-snapshot window is real but narrow -- e.g. a
        # pure slot-position swap (a hotbar tool-switch) between a real
        # pickup and whenever something checks can evict that pickup's
        # "gained" status even though the item is still sitting right
        # there in inventory (confirmed live chasing a report of !give
        # saying "I haven't picked up anything" right after a !collect
        # pickup). Callers that need to reliably confirm "did item X's
        # count actually increase since some earlier point" (MiningController.
        # collect, notably) should snapshot count_of(item) themselves
        # before the action and compare directly afterward, not rely on
        # gained_items() alone across a gap of unknown length.
        log.debug("inventory event: previous=%s current=%s gained=%s", self._previous, self._current, self.gained_items())
        for callback in self._on_change:
            callback()

    def _totals_by_item(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for entry in self._by_slot.values():
            totals[entry.item] = totals.get(entry.item, 0) + entry.count
        return totals

    @property
    def selected_slot(self) -> int | None:
        return self._selected_slot

    @property
    def last_gained_item(self) -> str | None:
        """The item whose total count is higher in the most recent
        `inventory` snapshot than in the one before it -- None if nothing
        increased. If more than one item increased between the same two
        snapshots, which one this returns is arbitrary; callers that need
        to detect and handle that ambiguity (rather than silently guess)
        should use gained_items() instead.
        """
        gained = self.gained_items()
        return next(iter(gained), None)

    def gained_items(self) -> list[str]:
        """Every item whose total count increased between the last two
        `inventory` snapshots, in no particular order.
        """
        return [item for item, count in self._current.items() if count > self._previous.get(item, 0)]

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
