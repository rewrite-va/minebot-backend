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

import asyncio
import logging

from minebot.actions.registry import ActionRegistry
from minebot.bot.movement import MovementController
from minebot.bridge.client import ModBridge, ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.config import BotConfig
from minebot.llm.controller import LLMController
from minebot.llm.trigger import should_trigger_llm
from minebot.mod_version import check_hello
from minebot.timing import log_timing, now

log = logging.getLogger("minebot.run_loop")


async def run(
    bridge: ModBridge,
    actions: ActionRegistry,
    tracker: EntityTracker,
    inventory: InventoryTracker,
    llm: LLMController,
    config: BotConfig,
    movement: MovementController,
    self_position: SelfPositionTracker,
) -> None:
    """Splits reading the mod's events from processing them into two
    concurrent tasks joined by a queue -- found live that a single
    combined loop deadlocks itself: !find's handler suspends the very
    same coroutine that reads bridge.events(), awaiting a find_result
    event that can now never be read off the socket to unblock it (the
    mod actually replies within milliseconds -- confirmed with
    monotonic-clock instrumentation showing the reply sitting fully
    received on the wire for a full 10 seconds, unread, until find()'s own
    timeout gave up and the loop finally got back around to it).

    _read_events (below) never blocks on processing -- it only ever
    awaits the next raw event and immediately either fast-paths it
    (find_result, resolved into movement's pending future the instant
    it's read) or queues it. The sequential processing loop below
    consumes that queue exactly as a single combined loop did before,
    preserving today's in-order guarantees for everything else (e.g.
    !follow's send_follow always happens-before a later entity-reconnect
    event is processed).
    """
    queue: asyncio.Queue[ModEvent] = asyncio.Queue()
    reader = asyncio.ensure_future(_read_events(bridge, movement, queue))

    try:
        while True:
            if queue.empty() and reader.done():
                # The reader finished (mod's event stream ended) and every
                # event it ever queued has already been processed -- stop.
                # Checking queue.empty() first (not just reader.done())
                # matters: the reader can finish while events it already
                # queued are still waiting to be processed, and those must
                # not be dropped.
                reader.result()  # re-raise if the reader crashed rather than ended cleanly
                break

            get_event = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({reader, get_event}, return_when=asyncio.FIRST_COMPLETED)
            if get_event not in done:
                get_event.cancel()
                continue  # reader finished/crashed -- loop back to the queue.empty()/reader.done() check above

            event = get_event.result()
            await _process_event(event, bridge, actions, tracker, inventory, llm, config, movement, self_position)
    finally:
        if not reader.done():
            reader.cancel()


async def _read_events(bridge: ModBridge, movement: MovementController, queue: asyncio.Queue[ModEvent]) -> None:
    """Continuously drains bridge.events() -- this is the only coroutine
    that ever awaits the next raw WebSocket message, so it must never
    block on anything that itself waits for a *later* event (that's
    exactly the deadlock run()'s docstring describes). find_result and
    arrived both get a fast-path straight to movement.on_find_result/
    on_arrived here, before the event even reaches the queue, since a
    command handler (find()) may be suspended waiting specifically for
    one of those calls.
    """
    async for event in bridge.events():
        log_timing(log, "read @ %.3f: type=%s", now(), event.type)
        if event.type == "find_result":
            movement.on_find_result(event.data)
        elif event.type == "arrived":
            movement.on_arrived()
        await queue.put(event)


async def _process_event(
    event: ModEvent,
    bridge: ModBridge,
    actions: ActionRegistry,
    tracker: EntityTracker,
    inventory: InventoryTracker,
    llm: LLMController,
    config: BotConfig,
    movement: MovementController,
    self_position: SelfPositionTracker,
) -> None:
    """Handles every event type except find_result/arrived, which
    _read_events already resolves (movement.on_find_result/on_arrived)
    before queuing (see run()'s docstring) -- they still flow through this
    queue like any other event, but nothing here needs to react to them a
    second time.
    """
    log_timing(log, "processing @ %.3f: type=%s", now(), event.type)
    if event.type in ("find_result", "arrived"):
        return

    if event.type == "hello":
        check_hello(event.data.get("commit", "unknown"), event.data.get("built_at", "unknown"), config.mod_repo_path)
        return

    if event.type == "entity":
        log.debug("entity event: %s", event.data)
        tracker.handle_event(event)
        if event.data.get("action") == "add":
            await movement.on_entity_added(event.data.get("name"), event.data.get("id"))
        return

    if event.type == "inventory":
        log.debug("inventory event: %s", event.data)
        inventory.handle_event(event)
        return

    if event.type == "chat":
        sender = event.data.get("sender")
        text = event.data.get("text", "")
        log.info("<%s> %s", sender or "system", text)

        if sender is not None and sender == self_position.own_name:
            # The mod hears its own chat messages the same as anyone
            # else's (they go through the normal server chat
            # broadcast) -- without this guard, a command's own error
            # reply ("something went wrong running !find") got
            # re-parsed as a fresh !find with no args, which itself
            # errored and replied again, forever (found live: an
            # infinite crash loop from a single mistyped command).
            return

        await _handle_chat(bridge, actions, llm, config, text, sender)
        return

    if event.type == "health":
        log.debug("health: %s", event.data.get("health"))
        return

    if event.type == "death":
        log.info("we died")
        return

    if event.type == "respawn":
        log.info("respawned")
        return

    if event.type == "position":
        log.debug(
            "position: (%.2f, %.2f, %.2f) yaw=%.1f on_ground=%s",
            event.data.get("x", 0.0), event.data.get("y", 0.0), event.data.get("z", 0.0),
            event.data.get("yaw", 0.0), event.data.get("on_ground"),
        )
        self_position.handle_event(event)
        return


async def _handle_chat(
    bridge: ModBridge, actions: ActionRegistry, llm: LLMController, config: BotConfig, text: str, sender: str | None,
) -> None:
    """The actual chat-dispatch logic, split out of _process_event for
    readability. Runs sequentially with everything else (see run()'s
    docstring) -- a slow command here does delay later events from being
    processed, same as before this file's reader/processor split, just no
    longer able to deadlock itself on a reply only the reader can deliver.
    """
    try:
        result = await actions.dispatch_chat(text, sender)
        if result is not None:
            if result.message:
                await _send_chat_reply(bridge, result.message)
            else:
                log.debug("command !%s ran with no reply message", text.strip().lstrip("!"))
            return

        if text.strip().startswith("!"):
            await _send_chat_reply(bridge, f"unknown command: {text}")
            return

        if should_trigger_llm(text, sender, config.bot_name, config.trigger_words):
            await llm.handle_chat(sender, text)
    except Exception:
        log.exception("unhandled error handling chat %r from %r", text, sender)


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
