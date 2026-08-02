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
        finally:
            log.info("minebot-mod disconnected")
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
        """
        while True:
            await self._connected.wait()
            connection = self._connection
            if connection is None:
                continue
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
        return ModEvent(type=event_type, data=data)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._connection is None:
            log.debug("dropping command, mod not connected: %r", payload)
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
