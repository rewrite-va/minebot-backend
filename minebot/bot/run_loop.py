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
    self_position: SelfPositionTracker,
) -> None:
    """Splits reading the mod's events from processing them into two
    concurrent tasks joined by a queue -- found live (back when !find
    existed) that a single combined loop can deadlock itself: a handler
    that suspends awaiting a later event can starve the very coroutine
    that reads bridge.events(), since nothing else is left to read the
    reply that would unblock it.

    _read_events (below) never blocks on processing -- it only ever
    awaits the next raw event and immediately either fast-paths it or
    queues it. State-tracking events (entity/inventory/position) are
    fast-pathed synchronously in _read_events itself, so every command
    handler always sees fully up-to-date tracker state regardless of how
    concurrently chat commands end up scheduled (see the
    chat-command-cancellation note below for why that matters).

    Chat commands are dispatched as their own cancellable task, not
    awaited inline -- a long-running command must not leave the bot
    unresponsive to !follow/!stop typed while it's still running. A
    newly-arrived chat command cancels whatever chat-command task is
    still in flight before starting its own (see _dispatch_chat_command/
    current_command_task below) -- the *mod*-side goal a superseded
    command was driving gets naturally superseded too, since
    ControlState's setFollow/etc. unconditionally overwrite whatever mode
    was previously active (confirmed in minebot-mod's ControlState.java --
    there is no separate "cancel current goal first" step needed
    mod-side, a fresh command already wins outright).

    This used to be unsafe for a different reason (see FINDINGS.md's
    "background-task approach... rejected" note): backgrounding a chat
    command's *entire* dispatch, including the tracker updates it used to
    read/write inline via _process_event, could let a later entity event
    race ahead of an earlier chat command's own tracker reads (e.g.
    !follow resolving a player name via EntityTracker.find_by_name). That
    hazard is gone now that entity/inventory/position events are
    fast-pathed synchronously in _read_events instead of flowing through
    chat-command-adjacent concurrency at all -- a command handler that
    runs later always sees a tracker state that's already fully caught up
    to every event received so far, independent of whichever order
    commands themselves finish executing in.
    """
    queue: asyncio.Queue[ModEvent] = asyncio.Queue()
    reader = asyncio.ensure_future(_read_events(bridge, tracker, inventory, self_position, queue))
    current_command_task: asyncio.Task | None = None

    try:
        while True:
            if queue.empty() and reader.done():
                # The reader finished (mod's event stream ended) and every
                # event it ever queued has already been processed -- stop.
                # Checking queue.empty() first (not just reader.done())
                # matters: the reader can finish while events it already
                # queued are still waiting to be processed, and those must
                # not be dropped. Also wait out (not cancel) whatever chat
                # command is still running -- a graceful stream end should
                # let the last dispatched command actually finish, same as
                # every earlier event already got processed to completion
                # before this point; only run()'s own `finally` cancels an
                # in-flight command, and only for a genuinely abnormal exit
                # (an exception, or the reader itself crashing).
                if current_command_task is not None:
                    await current_command_task
                reader.result()  # re-raise if the reader crashed rather than ended cleanly
                break

            get_event = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({reader, get_event}, return_when=asyncio.FIRST_COMPLETED)
            if get_event not in done:
                get_event.cancel()
                continue  # reader finished/crashed -- loop back to the queue.empty()/reader.done() check above

            event = get_event.result()

            if event.type == "chat":
                current_command_task = await _dispatch_chat_command(
                    event, bridge, actions, llm, config, self_position, current_command_task,
                )
                continue

            await _process_event(event, config)
    finally:
        if not reader.done():
            reader.cancel()
        if current_command_task is not None and not current_command_task.done():
            current_command_task.cancel()


async def _read_events(
    bridge: ModBridge,
    tracker: EntityTracker,
    inventory: InventoryTracker,
    self_position: SelfPositionTracker,
    queue: asyncio.Queue[ModEvent],
) -> None:
    """Continuously drains bridge.events() -- this is the only coroutine
    that ever awaits the next raw WebSocket message, so it must never
    block on anything that itself waits for a *later* event (that's
    exactly the deadlock run()'s docstring describes).

    Fast-paths state-tracking events synchronously (no suspension point
    beyond the occasional `await` that only ever *sends*, never waits for
    a reply): entity/inventory/position update their trackers
    immediately -- fast-pathing these here, not through the queue, is
    what makes chat-command dispatch safe to run as independent
    concurrent tasks (see run()'s own docstring): every tracker read a
    command handler ever does is guaranteed current as of every event
    received so far, regardless of which order concurrently-running
    command tasks happen to finish in.
    """
    async for event in bridge.events():
        log_timing(log, "read @ %.3f: type=%s", now(), event.type)
        if event.type == "entity":
            log.debug("entity event: %s", event.data)
            tracker.handle_event(event)
        elif event.type == "inventory":
            log.debug("inventory event: %s", event.data)
            inventory.handle_event(event)
        elif event.type == "position":
            log.debug(
                "position: (%.2f, %.2f, %.2f) yaw=%.1f on_ground=%s",
                event.data.get("x", 0.0), event.data.get("y", 0.0), event.data.get("z", 0.0),
                event.data.get("yaw", 0.0), event.data.get("on_ground"),
            )
            self_position.handle_event(event)
        else:
            await queue.put(event)


async def _process_event(event: ModEvent, config: BotConfig) -> None:
    """Handles whatever's left after _read_events' fast-path -- hello/
    health/death/respawn. Everything state-tracking (entity/inventory/
    position) and every pending-future resolver is already handled
    synchronously in _read_events; chat is dispatched separately by
    run()'s own _dispatch_chat_command, not routed through here at all.
    """
    log_timing(log, "processing @ %.3f: type=%s", now(), event.type)

    if event.type == "hello":
        check_hello(event.data.get("commit", "unknown"), event.data.get("built_at", "unknown"), config.mod_repo_path)
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


async def _dispatch_chat_command(
    event: ModEvent,
    bridge: ModBridge,
    actions: ActionRegistry,
    llm: LLMController,
    config: BotConfig,
    self_position: SelfPositionTracker,
    current_command_task: asyncio.Task | None,
) -> asyncio.Task | None:
    """Cancels whatever chat-command task is still running, then starts
    this one as a fresh task and returns it (the new current_command_task
    for run()'s next iteration to track). A cancelled command's own
    cleanup still runs (any handler with a `finally` block still gets it)
    -- asyncio.CancelledError propagates through ActionRegistry.
    dispatch_chat's `except Exception` untouched (it's a BaseException,
    not caught there), so a superseded command unwinds cleanly rather
    than leaking any pending state.

    Only one command task is ever tracked at a time -- chat commands are
    typed by a human one at a time in practice, so "the newest command
    wins, cancelling whatever was running" is the intended behavior, not
    a queue of commands to run one after another.
    """
    sender = event.data.get("sender")
    text = event.data.get("text", "")
    log.info("<%s> %s", sender or "system", text)

    if sender is not None and sender == self_position.own_name:
        # The mod hears its own chat messages the same as anyone else's
        # (they go through the normal server chat broadcast) -- without
        # this guard, a command's own error reply ("something went wrong
        # running !follow") got re-parsed as a fresh command with no args,
        # which itself errored and replied again, forever (found live: an
        # infinite crash loop from a single mistyped command). Checked
        # *before* cancelling current_command_task, not just before
        # dispatch -- a long-running command's own progress replies
        # cancelling the very task that sent them would corrupt whatever
        # state that task was tracking, since the mod-side goal it was
        # driving is never told to stop on its own (nothing cancels
        # *that*, see this function's own docstring).
        return current_command_task

    if current_command_task is not None and not current_command_task.done():
        current_command_task.cancel()

    return asyncio.ensure_future(_handle_chat(bridge, actions, llm, config, text, sender))


async def _handle_chat(
    bridge: ModBridge, actions: ActionRegistry, llm: LLMController, config: BotConfig, text: str, sender: str | None,
) -> None:
    """The actual chat-dispatch logic -- runs as its own cancellable task
    (see _dispatch_chat_command), so a long-running command here no
    longer delays any other event from being read/processed, and can
    itself be interrupted by whatever chat command comes next.
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
    except asyncio.CancelledError:
        log.info("command !%s superseded by a newer command, cancelled", text.strip().lstrip("!"))
        raise
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
