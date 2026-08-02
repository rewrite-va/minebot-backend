"""Verifies ModBridge against a real (local, ephemeral) WebSocket server
standing in for minebot-mod's ControlServer -- exercises the actual wire
format, not just internal method calls.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from minebot.bridge.client import ModBridge


@pytest.mark.asyncio
async def test_send_methods_produce_correctly_shaped_json():
    received: list[dict] = []

    async def handler(websocket):
        async for raw in websocket:
            received.append(json.loads(raw))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        bridge = ModBridge("127.0.0.1", port)
        await bridge.connect()

        await bridge.send_goto(1.0, 2.0, 3.0, stop_distance=1.5)
        await bridge.send_follow(42, stop_distance=3.0)
        await bridge.send_stop()
        await bridge.send_chat("hello")

        await asyncio.sleep(0.1)  # let the server-side handler drain the messages
        await bridge.close()

    assert received == [
        {"type": "goto", "x": 1.0, "y": 2.0, "z": 3.0, "stop_distance": 1.5},
        {"type": "follow", "entity_id": 42, "stop_distance": 3.0},
        {"type": "stop"},
        {"type": "chat", "text": "hello"},
    ]


@pytest.mark.asyncio
async def test_events_parses_incoming_messages_into_mod_events():
    async def handler(websocket):
        await websocket.send(json.dumps({"type": "chat", "sender": "Alex", "text": "hi"}))
        await websocket.send(json.dumps({"type": "position", "x": 1.0}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        bridge = ModBridge("127.0.0.1", port)
        await bridge.connect()

        events = []
        async for event in bridge.events():
            events.append(event)
            if len(events) == 2:
                break

        await bridge.close()

    assert events[0].type == "chat"
    assert events[0].data == {"sender": "Alex", "text": "hi"}
    assert events[1].type == "position"
    assert events[1].data == {"x": 1.0}


@pytest.mark.asyncio
async def test_events_ignores_malformed_and_untyped_messages():
    async def handler(websocket):
        await websocket.send("not json at all")
        await websocket.send(json.dumps({"no_type_field": True}))
        await websocket.send(json.dumps({"type": "stop"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        bridge = ModBridge("127.0.0.1", port)
        await bridge.connect()

        events = []
        async for event in bridge.events():
            events.append(event)
            break

        await bridge.close()

    assert len(events) == 1
    assert events[0].type == "stop"
