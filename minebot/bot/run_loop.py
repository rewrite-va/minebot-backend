"""Main event loop: connects to minebot-mod's control channel and reacts
to whatever it reports (chat, entity, position, health events) -- the
Python-side equivalent of the old protocol implementation's PLAY loop, but
driven by the mod's JSON events instead of raw packets.

Chat dispatch order: try it as a !command first (ActionRegistry.
dispatch_chat), and only if that finds nothing, hand it to the LLM
trigger check (should_trigger_llm) -- a player addressing the bot by name
or a trigger word ("hey minebot, got food?") should still work as an LLM
conversation even though it isn't a !command, but an actual !command
always takes priority over LLM interpretation of the same text.
"""

from __future__ import annotations

import logging

from minebot.actions.registry import ActionRegistry
from minebot.bot.movement import MovementController
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController
from minebot.llm.trigger import should_trigger_llm
from minebot.mod_version import check_hello

log = logging.getLogger("minebot.run_loop")


async def run(
    bridge: ModBridge,
    actions: ActionRegistry,
    tracker: EntityTracker,
    inventory: InventoryTracker,
    llm: LLMController,
    config: BotConfig,
    movement: MovementController,
) -> None:
    async for event in bridge.events():
        if event.type == "hello":
            check_hello(event.data.get("commit", "unknown"), event.data.get("built_at", "unknown"), config.mod_repo_path)
            continue

        if event.type == "entity":
            log.debug("entity event: %s", event.data)
            tracker.handle_event(event)
            if event.data.get("action") == "add":
                await movement.on_entity_added(event.data.get("name"), event.data.get("id"))
            continue

        if event.type == "inventory":
            log.debug("inventory event: %s", event.data)
            inventory.handle_event(event)
            continue

        if event.type == "chat":
            sender = event.data.get("sender")
            text = event.data.get("text", "")
            log.info("<%s> %s", sender or "system", text)

            result = await actions.dispatch_chat(text, sender)
            if result is not None:
                if result.message:
                    await bridge.send_chat(result.message)
                continue

            if text.strip().startswith("!"):
                await bridge.send_chat(f"unknown command: {text}")
                continue

            if should_trigger_llm(text, sender, config.bot_name, config.trigger_words):
                await llm.handle_chat(sender, text)
            continue

        if event.type == "health":
            log.debug("health: %s", event.data.get("health"))
            continue

        if event.type == "death":
            log.info("we died")
            continue

        if event.type == "respawn":
            log.info("respawned")
            continue

        if event.type == "position":
            log.debug(
                "position: (%.2f, %.2f, %.2f) yaw=%.1f on_ground=%s",
                event.data.get("x", 0.0), event.data.get("y", 0.0), event.data.get("z", 0.0),
                event.data.get("yaw", 0.0), event.data.get("on_ground"),
            )
            continue
