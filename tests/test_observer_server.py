"""Verifies ObserverServer against a real websockets.connect() client
standing in for a frontend viewer, and ModBridge's own send/recv paths
against a real ObserverServer -- same real-socket testing shape
test_mod_bridge.py already uses for the control channel itself.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from minebot.bridge.client import ModBridge
from minebot.bridge.observer import ObserverServer


async def _connect(server) -> str:
    while server._server is None:
        await asyncio.sleep(0.01)
    port = server._server.sockets[0].getsockname()[1]
    return f"ws://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_broadcast_reaches_a_connected_observer():
    server = ObserverServer("127.0.0.1", 0)
    await server.start()
    url = await _connect(server)

    async with websockets.connect(url) as client:
        # Give _on_connection a moment to actually register the
        # connection before broadcasting, same as ModBridge's own tests
        # do for its control-channel connection.
        await asyncio.sleep(0.05)
        server.broadcast("sent", {"type": "stop"})
        raw = await asyncio.wait_for(client.recv(), timeout=2.0)

        await server.close()

    data = json.loads(raw)
    assert data["direction"] == "sent"
    assert data["message"] == {"type": "stop"}
    assert isinstance(data["timestamp"], float)


@pytest.mark.asyncio
async def test_broadcast_with_no_observers_connected_does_not_raise():
    server = ObserverServer("127.0.0.1", 0)
    await server.start()

    server.broadcast("received", {"type": "health", "health": 20.0})  # nobody's listening -- must not raise

    await server.close()


@pytest.mark.asyncio
async def test_broadcast_reaches_multiple_observers():
    server = ObserverServer("127.0.0.1", 0)
    await server.start()
    url = await _connect(server)

    async with websockets.connect(url) as client_a, websockets.connect(url) as client_b:
        await asyncio.sleep(0.05)
        server.broadcast("sent", {"type": "chat", "text": "hi"})

        raw_a = await asyncio.wait_for(client_a.recv(), timeout=2.0)
        raw_b = await asyncio.wait_for(client_b.recv(), timeout=2.0)

        await server.close()

    assert json.loads(raw_a)["message"] == {"type": "chat", "text": "hi"}
    assert json.loads(raw_b)["message"] == {"type": "chat", "text": "hi"}


@pytest.mark.asyncio
async def test_mod_bridge_broadcasts_sent_and_received_messages_to_the_observer():
    observer = ObserverServer("127.0.0.1", 0)
    await observer.start()
    observer_url = await _connect(observer)

    bridge = ModBridge("127.0.0.1", 0, observer)
    connect_task = asyncio.create_task(bridge.connect())
    while bridge._server is None:
        await asyncio.sleep(0.01)
    mod_port = bridge._server.sockets[0].getsockname()[1]

    async with websockets.connect(observer_url) as observer_client:
        await asyncio.sleep(0.05)

        async with websockets.connect(f"ws://127.0.0.1:{mod_port}") as mod_client:
            await connect_task

            await bridge.send_stop()
            sent_raw = await asyncio.wait_for(observer_client.recv(), timeout=2.0)

            await mod_client.send(json.dumps({"type": "health", "health": 15.0}))
            # Drain the event so _parse_event actually runs (broadcasting
            # to the observer happens there, not in events()'s consumer).
            async for _event in bridge.events():
                break
            received_raw = await asyncio.wait_for(observer_client.recv(), timeout=2.0)

            await bridge.close()

        await observer.close()

    sent = json.loads(sent_raw)
    received = json.loads(received_raw)
    assert sent == {"direction": "sent", "message": {"type": "stop"}, "timestamp": sent["timestamp"]}
    assert received["direction"] == "received"
    assert received["message"] == {"type": "health", "health": 15.0}


@pytest.mark.asyncio
async def test_mod_bridge_works_with_no_observer_configured():
    bridge = ModBridge("127.0.0.1", 0)  # observer omitted entirely
    await bridge.send_stop()  # must not raise even with nothing connected and no observer at all
