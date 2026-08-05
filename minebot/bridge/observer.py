"""Broadcasts every wire message ModBridge sends/receives to any number of
connected observer clients (e.g. the minebot-frontend live wire-message
viewer) -- a separate WebSocket server from the control channel itself
(ModBridge's own server, which only ever accepts the one connection that
matters: minebot-mod). Deliberately independent of DEBUG/timing.py's
log_timing gate and the logging system generally: those write to a local
file for a human to read after the fact, this pushes structured JSON to
any live browser tab watching right now, and unlike the control channel,
more than one observer client can be connected at once (there's no
"only one thing that matters" constraint the way there is for the mod
connection).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal

from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger("minebot.observer")

Direction = Literal["sent", "received"]


class ObserverServer:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._connections: set[ServerConnection] = set()
        self._server = None

    async def start(self) -> None:
        self._server = await serve(self._on_connection, self._host, self._port)
        log.info("wire-message observer listening on %s:%s", self._host, self._port)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _on_connection(self, connection: ServerConnection) -> None:
        log.info("observer client connected (%s)", connection.remote_address)
        self._connections.add(connection)
        try:
            await connection.wait_closed()
        finally:
            self._connections.discard(connection)
            log.info("observer client disconnected (%s)", connection.remote_address)

    def broadcast(self, direction: Direction, message: dict[str, Any]) -> None:
        """Fire-and-forget to every currently-connected observer -- safe to
        call with zero observers connected (the common case: nobody has
        the frontend open most of the time), and never awaited by the
        caller, since a slow/stuck browser tab must never be able to
        backpressure real command dispatch or event processing.
        """
        if not self._connections:
            return
        payload = json.dumps({"direction": direction, "message": message, "timestamp": time.time()})
        for connection in list(self._connections):
            asyncio.ensure_future(self._send_one(connection, payload))

    async def _send_one(self, connection: ServerConnection, payload: str) -> None:
        try:
            await connection.send(payload)
        except Exception:
            log.debug("failed to send to an observer client (likely disconnected)", exc_info=True)
