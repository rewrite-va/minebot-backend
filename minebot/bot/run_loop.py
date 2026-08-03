"""Main event loop: connects to minebot-mod's control channel and reacts
to whatever it reports (chat, entity, position, health events) -- the
Python-side equivalent of the old protocol implementation's PLAY loop, but
driven by the mod's JSON events instead of raw packets.
"""

from __future__ import annotations

import logging

from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.commands.registry import CommandRegistry

log = logging.getLogger("minebot.run_loop")


async def run(
    bridge: ModBridge, commands: CommandRegistry, tracker: EntityTracker, inventory: InventoryTracker,
) -> None:
    async for event in bridge.events():
        if event.type == "entity":
            log.debug("entity event: %s", event.data)
            tracker.handle_event(event)
            continue

        if event.type == "inventory":
            log.debug("inventory event: %s", event.data)
            inventory.handle_event(event)
            continue

        if event.type == "chat":
            sender = event.data.get("sender")
            text = event.data.get("text", "")
            log.info("<%s> %s", sender or "system", text)
            dispatched = await commands.dispatch(text, sender)
            if not dispatched and text.strip().startswith("!"):
                await bridge.send_chat(f"unknown command: {text}")
            continue

        if event.type == "health":
            health = event.data.get("health")
            if health is not None and health <= 0.0:
                log.info("we died (health=%s)", health)
            continue

        if event.type == "position":
            log.debug(
                "position: (%.2f, %.2f, %.2f) yaw=%.1f on_ground=%s",
                event.data.get("x", 0.0), event.data.get("y", 0.0), event.data.get("z", 0.0),
                event.data.get("yaw", 0.0), event.data.get("on_ground"),
            )
            continue
