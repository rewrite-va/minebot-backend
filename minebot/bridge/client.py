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

from minebot.timing import log_timing, now

log = logging.getLogger("minebot.bridge")


@dataclass(frozen=True)
class ModEvent:
    type: str
    data: dict[str, Any]


class ModBridge:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._connection: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._server = None

    async def connect(self) -> None:
        """Starts listening and waits for the mod to connect in. Named to
        match the previous client-side API (main.py/run_loop.py don't need
        to know which side of the WebSocket handshake we're on).
        """
        self._server = await serve(self._on_connection, self._host, self._port)
        log.info("listening for minebot-mod on %s:%s", self._host, self._port)
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
        return ModEvent(type=event_type, data=data)

    async def _send(self, payload: dict[str, Any]) -> None:
        log_timing(log, "send @ %.3f: %s", now(), payload)
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

    async def send_goto(self, x: float, y: float, z: float, stop_distance: float = 2.0) -> None:
        await self._send({"type": "goto", "x": x, "y": y, "z": z, "stop_distance": stop_distance})

    async def send_follow(self, entity_id: int, stop_distance: float = 2.0) -> None:
        await self._send({"type": "follow", "entity_id": entity_id, "stop_distance": stop_distance})

    async def send_stop(self) -> None:
        await self._send({"type": "stop"})

    async def send_chat(self, text: str) -> None:
        await self._send({"type": "chat", "text": text})

    async def send_move_to_hotbar(self, slot: int, hotbar_slot: int) -> None:
        await self._send({"type": "move_to_hotbar", "slot": slot, "hotbar_slot": hotbar_slot})

    async def send_equip(self, slot: int) -> None:
        await self._send({"type": "equip", "slot": slot})

    async def send_drop(self, slot: int, count: int) -> None:
        await self._send({"type": "drop", "slot": slot, "count": count})

    async def send_give(self, entity_id: int, slot: int, count: int, stop_distance: float = 2.0) -> None:
        await self._send({
            "type": "give", "entity_id": entity_id, "slot": slot, "count": count, "stop_distance": stop_distance,
        })

    async def send_find(self, query: str, radius: int = 64) -> None:
        await self._send({"type": "find", "query": query, "radius": radius})

    async def send_dig_down(self, count: int) -> None:
        await self._send({"type": "dig_down", "count": count})

    async def send_collect(self, query: str, radius: int = 64) -> None:
        await self._send({"type": "collect", "query": query, "radius": radius})

    async def send_query(self, sub_type: str, arguments: list[str], key: str) -> None:
        await self._send({"type": "query", "sub_type": sub_type, "arguments": arguments, "key": key})

    async def send_debug_swap_test(self) -> None:
        """Temporary !debug command -- see minebot-mod's runDebugSwapTest
        and FINDINGS.md's "InventoryActions.moveToHotbar's local-only
        swap genuinely desyncing the server's view of the held item"
        section. Tests InventoryActions.moveToHotbar in isolation (no
        mining involved at all): shift-clicks the diamond pickaxe out of
        the hotbar into main storage first (a real, server-synced
        container click), then calls moveToHotbar to bring it back into
        hotbar slot 0 -- the exact mechanism suspected of desyncing the
        server's view of the bot's held item from what other clients
        actually see it holding."""
        await self._send({"type": "debug_swap_test"})
