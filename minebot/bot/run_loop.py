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
                    await _send_chat_reply(bridge, result.message)
                else:
                    log.debug("command !%s ran with no reply message", text.strip().lstrip("!"))
                continue

            if text.strip().startswith("!"):
                await _send_chat_reply(bridge, f"unknown command: {text}")
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


async def _send_chat_reply(bridge: ModBridge, message: str) -> None:
    """Wraps bridge.send_chat with explicit before/after/exception logging
    -- added to debug a live report of "!follow gives no confirmation or
    error in chat" with nothing in the log to explain why. ModBridge._send
    already logs (at WARNING for chat specifically) when it drops a
    command because the mod isn't currently connected, but a `.send()`
    call on a connection object that still *exists* but whose underlying
    socket is already closing raises instead of hitting that check --
    previously unhandled here, so such an exception would propagate
    straight out of the run loop with no attribution to "a chat reply
    failed to send", potentially ending run() entirely with a bare
    traceback and no clear signal of which reply was lost.
    """
    log.debug("sending chat reply: %r", message)
    try:
        await bridge.send_chat(message)
    except Exception:
        log.exception("failed to send chat reply: %r", message)
    else:
        log.debug("sent chat reply: %r", message)
