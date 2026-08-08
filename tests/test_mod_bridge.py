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

        await bridge.send_follow("Alex", stop_distance=3.0)
        await bridge.send_stop()
        await bridge.send_give(7, "minecraft:diamond", 5)
        # send_chat only enqueues (see its own docstring) -- doesn't wait
        # for its actual turn through the rate-limited drain queue, so it
        # isn't guaranteed to arrive in the same relative order as the
        # two synchronous sends above. Received separately below instead.
        await bridge.send_chat("hello")

        for _ in range(3):
            received.append(json.loads(await mod_client.recv()))
        chat_received = json.loads(await asyncio.wait_for(mod_client.recv(), timeout=2.0))

        await bridge.close()

    assert received == [
        {"type": "follow", "player_name": "Alex", "stop_distance": 3.0},
        {"type": "stop"},
        {"type": "give", "recipient_entity_id": 7, "item": "minecraft:diamond", "quantity": 5},
    ]
    assert chat_received == {"type": "chat", "text": "hello"}


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


@pytest.mark.asyncio
async def test_send_chat_is_a_no_op_when_mod_not_connected():
    # send_chat's own queue/drain task never even started (connect() was
    # never called) -- must not raise, just silently accept the enqueue.
    bridge = ModBridge("127.0.0.1", 0)
    await bridge.send_chat("hello")


@pytest.mark.asyncio
async def test_send_chat_rate_limits_a_burst_without_dropping_any():
    """Regression test: InventoryAnnouncer firing once per gained item
    type (or a fresh reconnect re-announcing the whole inventory) used to
    queue up many real chat sends with zero throttling, which got the bot
    kicked from the server for spamming once minebot-mod's own "chat"
    command handler was reintroduced (see minebot-mod's MinebotMod.
    dispatchMessage). send_chat now enqueues instead of sending directly,
    and a background task drains at CHAT_RATE_PER_SECOND -- this asserts
    a burst of 5 messages all eventually arrive, in order, spaced out
    rather than dropped or sent all at once.
    """
    bridge = ModBridge("127.0.0.1", 0)
    connect_task = asyncio.create_task(bridge.connect())

    while bridge._server is None:
        await asyncio.sleep(0.01)
    port = bridge._server.sockets[0].getsockname()[1]

    async with websockets.connect(f"ws://127.0.0.1:{port}") as mod_client:
        await connect_task

        messages = [f"message {i}" for i in range(5)]
        for message in messages:
            await bridge.send_chat(message)  # returns immediately for all 5

        received = []
        for _ in range(5):
            received.append(json.loads(await asyncio.wait_for(mod_client.recv(), timeout=5.0))["text"])

        await bridge.close()

    assert received == messages


@pytest.mark.asyncio
async def test_events_recovers_after_a_reconnect_without_busy_looping():
    """Regression test: events() used to busy-loop at 100% CPU after a
    client disconnected and reconnected, because it could observe the old
    (closed) connection object as still current -- a race against
    _on_connection's own cleanup -- and re-iterate it instantly forever
    instead of waiting for a genuinely new connection. Found live: the
    backend process kept climbing in CPU usage with no further log output
    after "connection closed". events() now tracks the last connection
    object it actually iterated and refuses to re-enter the same one,
    sleeping briefly instead.
    """
    bridge = ModBridge("127.0.0.1", 0)
    connect_task = asyncio.create_task(bridge.connect())

    while bridge._server is None:
        await asyncio.sleep(0.01)
    port = bridge._server.sockets[0].getsockname()[1]

    events_task = asyncio.create_task(_collect_two_events(bridge))

    first_client = await websockets.connect(f"ws://127.0.0.1:{port}")
    await connect_task
    await first_client.send(json.dumps({"type": "chat", "sender": "Alex", "text": "first"}))
    await first_client.close()

    # Reconnect as a fresh client, standing in for the mod reconnecting
    # after a drop -- this must be picked up promptly, not starved by a
    # busy loop spinning on the now-closed first connection.
    second_client = await websockets.connect(f"ws://127.0.0.1:{port}")
    await second_client.send(json.dumps({"type": "chat", "sender": "Alex", "text": "second"}))

    events = await asyncio.wait_for(events_task, timeout=2.0)

    await second_client.close()
    await bridge.close()

    assert [e.data["text"] for e in events] == ["first", "second"]


async def _collect_two_events(bridge: ModBridge) -> list:
    events = []
    async for event in bridge.events():
        events.append(event)
        if len(events) == 2:
            return events
    return events
