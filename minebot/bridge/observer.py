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
from pathlib import PurePosixPath
from typing import Any, Literal

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

log = logging.getLogger("minebot.observer")

Direction = Literal["sent", "received"]


def _json_response(body: bytes) -> Response:
    headers = Headers()
    headers["Content-Type"] = "application/json"
    # Frontend dev server runs on a different origin/port than this server
    # -- a plain fetch() would otherwise be blocked by CORS.
    headers["Access-Control-Allow-Origin"] = "*"
    return Response(200, "OK", headers, body)


class ObserverServer:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._connections: set[ServerConnection] = set()
        self._server = None

    async def start(self) -> None:
        self._server = await serve(self._on_connection, self._host, self._port, process_request=self._process_request)
        log.info("wire-message observer listening on %s:%s", self._host, self._port)

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        """Intercepts plain HTTP requests before the WS handshake -- lets
        minebot-frontend's replay viewer fetch `GET /replays` (listing) and
        `GET /replays/<filename>` (one full replay) off the SAME port the
        live wire feed already uses, rather than standing up a second
        server just for this. Returning None here (any other path) falls
        through to the normal WS upgrade, unmodified.
        """
        if request.path == "/replays":
            return self._list_replays()
        if request.path.startswith("/replays/"):
            filename = request.path.removeprefix("/replays/")
            return self._get_replay(filename)
        return None

    def _replay_dir(self):
        # Local import -- minebot.testing.replay's own ReplayRecorder
        # type-hints ModEvent (from this module), so importing it at
        # module scope here would be a real circular import.
        from minebot.testing.replay import replay_output_dir

        return replay_output_dir()

    def _list_replays(self) -> Response:
        replay_dir = self._replay_dir()
        entries: list[dict[str, Any]] = []
        if replay_dir.is_dir():
            paths = sorted(replay_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                try:
                    data = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    log.warning("skipping unreadable/malformed replay file %s", path)
                    continue
                metadata = dict(data.get("metadata", {}))
                metadata["filename"] = path.name
                entries.append(metadata)

        return _json_response(json.dumps(entries).encode())

    def _get_replay(self, filename: str) -> Response:
        # PurePosixPath.name strips any directory components a malicious/
        # malformed request path might smuggle in (e.g. "../../etc/passwd")
        # -- only a bare filename within replay_output_dir() is ever served.
        safe_name = PurePosixPath(filename).name
        path = self._replay_dir() / safe_name
        if not safe_name.endswith(".json") or not path.is_file():
            return Response(404, "Not Found", Headers(), b"")
        return _json_response(path.read_bytes())

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
