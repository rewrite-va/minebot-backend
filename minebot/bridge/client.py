"""WebSocket client for minebot-mod's local control channel (see that
repo's ControlServer/MinebotMod). This is the only thing the Python side
talks to -- the mod itself is the sole connection to the actual Minecraft
server, running inside a real client with real physics/collision, logged
in via normal Microsoft/Mojang auth. Python sends high-level goals
("follow this entity", "go here", "say this") and receives game events
(chat, position, entities, health) back as JSON, one object per message.

See /home/colaila/git/minebot's `pure-protocol-backend` branch for the
previous from-scratch protocol implementation this replaces.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets

log = logging.getLogger("minebot.bridge")


@dataclass(frozen=True)
class ModEvent:
    type: str
    data: dict[str, Any]


class ModBridge:
    def __init__(self, host: str, port: int) -> None:
        self._uri = f"ws://{host}:{port}"
        self._ws: websockets.ClientConnection | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._uri)
        log.info("connected to minebot-mod control channel at %s", self._uri)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def events(self) -> AsyncIterator[ModEvent]:
        """Yields every event the mod sends until the connection closes."""
        assert self._ws is not None, "call connect() first"
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ignoring malformed event from mod: %r", raw)
                continue
            event_type = data.pop("type", None)
            if event_type is None:
                log.warning("ignoring event with no 'type' field: %r", data)
                continue
            yield ModEvent(type=event_type, data=data)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._ws is not None, "call connect() first"
        await self._ws.send(json.dumps(payload))

    async def send_goto(self, x: float, y: float, z: float, stop_distance: float = 2.0) -> None:
        await self._send({"type": "goto", "x": x, "y": y, "z": z, "stop_distance": stop_distance})

    async def send_follow(self, entity_id: int, stop_distance: float = 2.0) -> None:
        await self._send({"type": "follow", "entity_id": entity_id, "stop_distance": stop_distance})

    async def send_stop(self) -> None:
        await self._send({"type": "stop"})

    async def send_chat(self, text: str) -> None:
        await self._send({"type": "chat", "text": text})
