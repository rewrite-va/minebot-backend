"""WebSocket server for minebot-mod's local control channel (see that
repo's ControlClient/MinebotMod). Python owns the server side and the mod
connects out to it, not the other way around -- found live that WSL2 (the
common home for this Python process) only forwards localhost connections
*from* Windows *into* WSL2, not the reverse, so a mod-side server was
unreachable no matter how it was bound; flipping the roles means the
mod's outbound connection to `localhost` gets transparently forwarded by
WSL2 instead, with no firewall changes or IP-passing needed.

This mod is still the only thing that talks to the actual Minecraft
server -- Python sends high-level goals ("follow this entity", "go here",
"say this") and receives game events (chat, position, entities, health)
back as JSON, one object per message, over whichever connection the mod
most recently opened.

See /home/colaila/git/minebot's `pure-protocol-backend` branch for the
previous from-scratch protocol implementation this replaces.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets
from websockets.asyncio.server import ServerConnection, serve

from minebot.bridge.observer import ObserverServer
from minebot.timing import log_timing, now

log = logging.getLogger("minebot.bridge")


@dataclass(frozen=True)
class ModEvent:
    type: str
    data: dict[str, Any]


class ModBridge:
    # Chat is a real vanilla chat SEND (see minebot-mod's own MinebotMod.
    # dispatchMessage "chat" case) -- visible to every player on the
    # server, subject to the SAME spam limits/kick risk a human typing too
    # fast would hit. Confirmed live: once that mod-side gap was fixed
    # (chat replies previously arrived at the mod but were silently never
    # displayed at all), a burst of unthrottled sends -- e.g.
    # InventoryAnnouncer firing once per gained item type, or a fresh
    # reconnect re-announcing the bot's entire inventory in one go -- got
    # the bot kicked for spamming. CHAT_RATE_PER_SECOND caps real sends to
    # a conservative, sustainable rate. Lowered from 2.0 to 1.0 -- even
    # this queue's own guaranteed-spread-out sends still got the bot
    # kicked for spamming at 2/sec during a real burst (confirmed live,
    # the same InventoryAnnouncer first-snapshot flood InventoryTracker's
    # own docstring describes -- that flood is separately fixed at the
    # source now, but the rate cap itself is still worth keeping
    # conservative for any future burst source).
    CHAT_RATE_PER_SECOND = 1.0

    def __init__(self, host: str, port: int, observer: ObserverServer | None = None) -> None:
        self._host = host
        self._port = port
        self._connection: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._server = None
        # Optional -- None means "nobody's watching", and every call site
        # below already no-ops cheaply in that case (see ObserverServer.
        # broadcast's own docstring), so this stays a plain attribute
        # rather than a null-object pattern.
        self._observer = observer
        # Real chat SENDS (not every _send call -- movement/combat
        # commands aren't rate-limited by the server the way chat is) go
        # through this queue instead of straight to _send, so a burst
        # never gets dropped -- see send_chat/_drain_chat_queue's own
        # docstrings for why this is a queue+fixed-interval drain (every
        # message eventually goes out, just spaced apart) rather than a
        # token-bucket-with-drop or a simple per-call rate check that
        # would silently lose messages during a burst.
        self._chat_queue: asyncio.Queue[str] = asyncio.Queue()
        self._chat_drain_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Starts listening and waits for the mod to connect in. Named to
        match the previous client-side API (main.py/run_loop.py don't need
        to know which side of the WebSocket handshake we're on).
        """
        self._server = await serve(self._on_connection, self._host, self._port)
        log.info("listening for minebot-mod on %s:%s", self._host, self._port)
        # Started here (not in __init__, which can't create tasks before
        # an event loop exists) and lives for the bridge's whole
        # connect()/close() lifetime, same bracketing main.py already uses
        # -- see close()'s own cleanup.
        self._chat_drain_task = asyncio.ensure_future(self._drain_chat_queue())
        await self._connected.wait()

    async def _on_connection(self, connection: ServerConnection) -> None:
        log.info("minebot-mod connected (%s)", connection.remote_address)
        self._connection = connection
        self._connected.set()
        try:
            await connection.wait_closed()
        except BaseException:
            log.exception("control-channel connection handler crashed")
            raise
        finally:
            log.info("minebot-mod disconnected (code=%s, reason=%r)", connection.close_code, connection.close_reason)
            if self._connection is connection:
                self._connection = None
                self._connected.clear()

    async def close(self) -> None:
        if self._chat_drain_task is not None:
            self._chat_drain_task.cancel()
            try:
                await self._chat_drain_task
            except asyncio.CancelledError:
                pass
            self._chat_drain_task = None
        if self._connection is not None:
            await self._connection.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def events(self) -> AsyncIterator[ModEvent]:
        """Yields every event the mod sends, reconnecting across drops
        (the mod itself retries on its side too -- see ControlClient) so a
        momentary disconnect doesn't end the whole run loop.

        Tracks the last connection object it actually finished iterating,
        since `_on_connection`'s cleanup (clearing `self._connection` /
        `self._connected`) runs concurrently and isn't guaranteed to happen
        before this loop notices the connection closed -- without this
        check, a closed-but-not-yet-cleared connection got re-read
        instantly on every spin, busy-looping at 100% CPU until the other
        coroutine's `finally` eventually ran (found live: a stale
        connection kept the process pegged at high CPU with no further log
        output after "connection closed").
        """
        last_connection = None
        while True:
            await self._connected.wait()
            connection = self._connection
            if connection is None or connection is last_connection:
                await asyncio.sleep(0.05)
                continue
            last_connection = connection
            try:
                async for raw in connection:
                    event = self._parse_event(raw)
                    if event is not None:
                        yield event
            except websockets.ConnectionClosed:
                pass

    def _parse_event(self, raw: str) -> ModEvent | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ignoring malformed event from mod: %r", raw)
            return None
        event_type = data.pop("type", None)
        if event_type is None:
            log.warning("ignoring event with no 'type' field: %r", data)
            return None
        # DEBUG=true-gated (see minebot/timing.py) -- added to debug a
        # suspected deadlock/reordering issue between !find's send and its
        # find_result reply (the mod replied within milliseconds on the
        # wire, but the reply wasn't *processed* until a 10s timeout
        # elsewhere gave up). Timestamps on every send/receive make actual
        # wall-clock gaps between "sent" and "received" (and between
        # "received" and "processed", logged separately by run_loop)
        # directly measurable instead of inferred from log line order alone.
        log_timing(log, "recv @ %.3f: type=%s %s", now(), event_type, data)
        if self._observer is not None:
            self._observer.broadcast("received", {"type": event_type, **data})
        return ModEvent(type=event_type, data=data)

    async def _send(self, payload: dict[str, Any]) -> None:
        log_timing(log, "send @ %.3f: %s", now(), payload)
        if self._observer is not None:
            self._observer.broadcast("sent", payload)
        if self._connection is None:
            # A dropped `chat` command is a real user-visible silent
            # failure (a player's command "worked" on the backend's side
            # -- the handler ran and returned a confirmation/error
            # message -- but they never saw a reply at all), typically
            # from a momentary reconnect window; found live, reported as
            # "not giving me back messages of confirmation or error"
            # with nothing in the log to explain why at the default INFO
            # level. Movement commands (goto/follow/stop) drop far more
            # routinely during normal reconnects (the mod resends its
            # last goal isn't tracked, but a dropped one is usually
            # superseded by the next tick's state anyway) so those stay
            # at debug to avoid spamming the log every reconnect.
            level = logging.WARNING if payload.get("type") == "chat" else logging.DEBUG
            log.log(level, "dropping command, mod not connected: %r", payload)
            return
        await self._connection.send(json.dumps(payload))

    async def send_follow(self, player_name: str, stop_distance: float = 2.0) -> None:
        await self._send({"type": "follow", "player_name": player_name, "stop_distance": stop_distance})

    async def send_stop(self) -> None:
        await self._send({"type": "stop"})

    async def send_kill(self, query: str | None = None, entity_id: int | None = None) -> None:
        await self._send({"type": "kill", "query": query, "entity_id": entity_id})

    async def send_defend(self, player_name: str | None = None) -> None:
        await self._send({"type": "defend", "player_name": player_name})

    async def send_pickup(self) -> None:
        await self._send({"type": "pickup"})

    async def send_sleep(self) -> None:
        await self._send({"type": "sleep"})

    async def send_goto(self, x: float, y: float, z: float) -> None:
        await self._send({"type": "goto", "x": x, "y": y, "z": z})

    async def send_query(
        self, arg: str, x: int | None = None, y: int | None = None, z: int | None = None,
    ) -> None:
        """Sends `{"type": "query", "arg": arg, ...}` -- MinebotMod.
        handleQuery replies with a `query_result` event (see
        QueryResultTracker's own docstring for how a caller actually waits
        for that reply). A read-only introspection request, not a real
        command with a game-world effect -- sent through the same unrated
        `_send` path as goto/follow/etc., never send_chat.

        `x`/`y`/`z` are optional extra fields, needed only by `arg="block"`
        (see MinebotMod.handleQuery's own docstring) -- every other `arg`
        value ignores them mod-side, so callers that don't need them
        simply omit all three.
        """
        payload: dict[str, object] = {"type": "query", "arg": arg}
        if x is not None:
            payload["x"] = x
        if y is not None:
            payload["y"] = y
        if z is not None:
            payload["z"] = z
        await self._send(payload)

    async def send_give(self, recipient_entity_id: int | None, item: str | None, quantity: int) -> None:
        """`recipient_entity_id`/`item` None and `quantity` 0 match
        minebot-mod's own Command.Give "give to the caller"/"the last item
        picked up"/"the whole stack" defaults exactly (see its own
        docstring) -- Python passes those through as-is rather than
        resolving them itself.
        """
        await self._send({
            "type": "give",
            "recipient_entity_id": recipient_entity_id,
            "item": item,
            "quantity": quantity,
        })

    async def send_chat(self, text: str) -> None:
        """Enqueues `text` for a real chat send and returns immediately --
        does NOT wait for its actual turn in the queue (see this class's
        own docstring for why: callers -- chat command replies,
        InventoryAnnouncer, the death announcer -- shouldn't block on
        chat throughput, and the queue guarantees every message eventually
        goes out in order regardless of how bursty the callers are).
        """
        await self._chat_queue.put(text)

    async def send_console_command(self, text: str) -> None:
        """Sends `text` (typically a `/`-command, e.g. `/fill ...`) the
        same way `send_chat` ultimately does -- as a real `{"type":
        "chat", "text": text}` wire message -- but goes straight to
        `_send`, bypassing CHAT_RATE_PER_SECOND/`_chat_queue` entirely.

        CHAT_RATE_PER_SECOND exists to keep the bot from getting kicked
        for spamming a real MULTIPLAYER server's chat (see that constant's
        own docstring) -- it does not apply here: this is specifically for
        world-setup/teardown commands sent to the disposable, single-
        player, integrated-server test world (see minebot-mod's
        TESTING.md), which has no other players to spam and no spam-kick
        risk at all. Per explicit direction: routing test-world `/fill`
        commands through the throttled queue was wrong, forcing artificial
        multi-second delays on every schematic placement for a risk that
        doesn't exist in that world. Ordinary gameplay chat/commands
        should still go through send_chat, not this.
        """
        await self._send({"type": "chat", "text": text})

    async def _drain_chat_queue(self) -> None:
        """Sends one queued chat message every 1/CHAT_RATE_PER_SECOND,
        forever, until cancelled (see close()'s own cleanup) -- a fixed-
        interval drain rather than a token-bucket-with-burst-allowance:
        simpler, and per explicit direction ("lets have a queue so we
        dont drop messages"), the actual requirement is "never drop, just
        spread out", which a steady drain satisfies directly without
        needing separate burst-capacity/refill-rate bookkeeping. Blocks on
        _chat_queue.get() when idle (no busy-polling) and reuses _send
        (not the raw connection) so observer broadcasting/the "mod not
        connected" drop-and-log path both still apply to queued chat the
        same as any other command.
        """
        interval = 1.0 / self.CHAT_RATE_PER_SECOND
        while True:
            text = await self._chat_queue.get()
            await self._send({"type": "chat", "text": text})
            await asyncio.sleep(interval)
