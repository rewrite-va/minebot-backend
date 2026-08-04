"""Announces real inventory gains to chat -- "I got a stone (12 total)" --
independent of any command that may have caused them. Deliberately
decoupled from !collect/!dig entirely: this reacts purely to
InventoryTracker's own change signal (see InventoryTracker.add_change_listener),
the same ground-truth source !collect's own drop-confirmation logic reads
from, so a gain gets announced whether it came from mining, killing
something, being given an item, or anything else -- the announcer has no
notion of "what command is currently running" at all.
"""

from __future__ import annotations

import asyncio
import logging

from minebot.bridge.client import ModBridge
from minebot.bridge.inventory import InventoryTracker

log = logging.getLogger("minebot.inventory_announcer")


class InventoryAnnouncer:
    def __init__(self, bridge: ModBridge, inventory: InventoryTracker) -> None:
        self.bridge = bridge
        self.inventory = inventory
        inventory.add_change_listener(self._on_change)

    def _on_change(self) -> None:
        gained = self.inventory.gained_items()
        for item in gained:
            total = self.inventory.count_of(item)
            name = item.removeprefix("minecraft:")
            log.info("inventory gain: %s (%d total)", name, total)
            asyncio.ensure_future(self.bridge.send_chat(f"I got a {name} ({total} total)"))
