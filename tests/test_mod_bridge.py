"""Verifies ModBridge (now the WebSocket *server* side, with minebot-mod
as the client -- see bridge/client.py's docstring for why) against a real
websockets.connect() client standing in for the mod, exercising the
actual wire format rather than just internal method calls.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from minebot.bridge.client import ModBridge


@pytest.mark.asyncio
async def test_send_methods_produce_correctly_shaped_json():
    bridge = ModBridge("127.0.0.1", 0)
    connect_task = asyncio.create_task(bridge.connect())

    # ModBridge.connect() blocks until a mod client connects in, so give it
    # a moment to start listening before we (as the fake mod) connect --
    # port 0 means an OS-assigned ephemeral port, so read it back off the
    # underlying server once it's up.
    while bridge._server is None:
        await asyncio.sleep(0.01)
    port = bridge._server.sockets[0].getsockname()[1]

    received: list[dict] = []
    async with websockets.connect(f"ws://127.0.0.1:{port}") as mod_client:
        await connect_task  # now resolves, since a connection just arrived

        await bridge.send_goto(1.0, 2.0, 3.0, stop_distance=1.5)
        await bridge.send_follow(42, stop_distance=3.0)
        await bridge.send_stop()
        await bridge.send_chat("hello")

        for _ in range(4):
            received.append(json.loads(await mod_client.recv()))

        await bridge.close()

    assert received == [
        {"type": "goto", "x": 1.0, "y": 2.0, "z": 3.0, "stop_distance": 1.5},
        {"type": "follow", "entity_id": 42, "stop_distance": 3.0},
        {"type": "stop"},
        {"type": "chat", "text": "hello"},
    ]


@pytest.mark.asyncio
async def test_events_parses_incoming_messages_into_mod_events():
    bridge = ModBridge("127.0.0.1", 0)
    connect_task = asyncio.create_task(bridge.connect())

    while bridge._server is None:
        await asyncio.sleep(0.01)
    port = bridge._server.sockets[0].getsockname()[1]

    async with websockets.connect(f"ws://127.0.0.1:{port}") as mod_client:
        await connect_task
        await mod_client.send(json.dumps({"type": "chat", "sender": "Alex", "text": "hi"}))
        await mod_client.send(json.dumps({"type": "position", "x": 1.0}))

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
    bridge = ModBridge("127.0.0.1", 0)
    connect_task = asyncio.create_task(bridge.connect())

    while bridge._server is None:
        await asyncio.sleep(0.01)
    port = bridge._server.sockets[0].getsockname()[1]

    async with websockets.connect(f"ws://127.0.0.1:{port}") as mod_client:
        await connect_task
        await mod_client.send("not json at all")
        await mod_client.send(json.dumps({"no_type_field": True}))
        await mod_client.send(json.dumps({"type": "stop"}))

        events = []
        async for event in bridge.events():
            events.append(event)
            break

        await bridge.close()

    assert len(events) == 1
    assert events[0].type == "stop"


@pytest.mark.asyncio
async def test_send_is_a_no_op_when_mod_not_connected():
    bridge = ModBridge("127.0.0.1", 0)
    # Never actually connected -- send_* must not raise, just drop the command.
    await bridge.send_stop()
