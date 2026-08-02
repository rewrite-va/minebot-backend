import asyncio

import pytest

from minebot.bot.movement import MovementController
from minebot.bot.play_loop import run_play_loop
from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.chunk_blocks import ChunkBlockCache
from minebot.protocol.chunks import ChunkHeightmapCache
from minebot.protocol.entities import EntityTracker
from minebot.protocol.registry import REGISTRY

STATE = "play"


def _encode_named_compound_entry(name: str, tag_type: int, payload: bytes) -> bytes:
    encoded_name = name.encode("utf-8")
    return bytes([tag_type]) + len(encoded_name).to_bytes(2, "big") + encoded_name + payload


def _encode_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(2, "big") + encoded


def _encode_plain_text_component_tag(text: str) -> bytes:
    TAG_STRING = 8
    TAG_COMPOUND = 10
    TAG_END = 0
    body = _encode_named_compound_entry("text", TAG_STRING, _encode_string(text))
    body += bytes([TAG_END])
    return bytes([TAG_COMPOUND]) + body


async def _fake_play_server(reader, writer, results: dict) -> None:
    conn = Connection(reader, writer)

    # Send a keepalive, expect an echo.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    body = ByteWriter()
    body.write_i64(99)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    results["keepalive_echo"] = ByteReader(raw.data).read_i64()

    # Send a system chat message containing a command; expect it dispatched
    # (the test's registered handler will reply over the same connection).
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SYSTEM_CHAT")
    body = ByteWriter()
    body.write(_encode_plain_text_component_tag("!ping"))
    body.write_bool(False)
    await conn.send_packet(packet_id, body.getvalue())

    # The !ping handler sends a chat command packet; read and record it.
    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["reply_packet_name"] = name
    results["reply_text"] = ByteReader(raw.data).read_utf()

    writer.close()


@pytest.mark.asyncio
async def test_play_loop_answers_keepalive_and_dispatches_commands():
    results: dict = {}
    calls = []

    async def ping_handler(conn, sender):
        calls.append("ping")
        from minebot.protocol.chat import send_say

        await send_say(conn, "pong")

    async def handler(reader, writer):
        await _fake_play_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    registry = CommandRegistry()
    registry.register("ping", ping_handler)
    tracker = EntityTracker()
    heightmaps = ChunkHeightmapCache()
    blocks = ChunkBlockCache()
    movement = MovementController(tracker, blocks)

    async with server:
        conn = await Connection.open(host, port)

        try:
            await asyncio.wait_for(
                run_play_loop(conn, registry, movement, tracker, heightmaps, blocks), timeout=1.0
            )
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass

        await conn.close()

    assert results["keepalive_echo"] == 99
    assert calls == ["ping"]
    assert results["reply_packet_name"] == "SERVERBOUND_CHAT_COMMAND"
    assert results["reply_text"] == "say pong"


async def _fake_death_server(reader, writer, results: dict) -> None:
    conn = Connection(reader, writer)

    # CLIENTBOUND_LOGIN, only the first field (playerId) matters/is sent.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LOGIN")
    body = ByteWriter()
    body.write_i32(555)
    await conn.send_packet(packet_id, body.getvalue())

    # Someone else's death (a different playerId) -- must NOT trigger a
    # respawn request.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PLAYER_COMBAT_KILL")
    body = ByteWriter()
    body.write_varint(999)
    await conn.send_packet(packet_id, body.getvalue())

    # Our own death -- must trigger a respawn request.
    body = ByteWriter()
    body.write_varint(555)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["respawn_packet_name"] = name
    results["respawn_action"] = ByteReader(raw.data).read_varint()

    writer.close()


@pytest.mark.asyncio
async def test_play_loop_respawns_only_on_our_own_death():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_death_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    registry = CommandRegistry()
    tracker = EntityTracker()
    heightmaps = ChunkHeightmapCache()
    blocks = ChunkBlockCache()
    movement = MovementController(tracker, blocks)

    async with server:
        conn = await Connection.open(host, port)

        try:
            await asyncio.wait_for(
                run_play_loop(conn, registry, movement, tracker, heightmaps, blocks), timeout=1.0
            )
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass

        await conn.close()

    assert results["respawn_packet_name"] == "SERVERBOUND_CLIENT_COMMAND"
    assert results["respawn_action"] == 0  # PERFORM_RESPAWN


async def _fake_health_death_server(reader, writer, results: dict) -> None:
    # Regression test: CLIENTBOUND_PLAYER_COMBAT_KILL is not sent for every
    # death (the real client detects death from health <= 0 in its own tick
    # loop, not a dedicated packet) -- a live-tested death was missed
    # entirely because we only listened for COMBAT_KILL.
    conn = Connection(reader, writer)

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LOGIN")
    body = ByteWriter()
    body.write_i32(555)
    await conn.send_packet(packet_id, body.getvalue())

    # Health drops to 0 with no combat-kill message at all.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SET_HEALTH")
    body = ByteWriter()
    body.write_f32(0.0)
    body.write_varint(20)
    body.write_f32(0.0)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["respawn_packet_name"] = name
    results["respawn_action"] = ByteReader(raw.data).read_varint()

    writer.close()


@pytest.mark.asyncio
async def test_play_loop_respawns_from_zero_health_alone():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_health_death_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    registry = CommandRegistry()
    tracker = EntityTracker()
    heightmaps = ChunkHeightmapCache()
    blocks = ChunkBlockCache()
    movement = MovementController(tracker, blocks)

    async with server:
        conn = await Connection.open(host, port)

        try:
            await asyncio.wait_for(
                run_play_loop(conn, registry, movement, tracker, heightmaps, blocks), timeout=1.0
            )
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass

        await conn.close()

    assert results["respawn_packet_name"] == "SERVERBOUND_CLIENT_COMMAND"
    assert results["respawn_action"] == 0


async def _fake_double_death_signal_server(reader, writer, results: dict) -> None:
    # Both SET_HEALTH(0) and COMBAT_KILL fire for the same death -- must
    # only send one PERFORM_RESPAWN, not two.
    conn = Connection(reader, writer)

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LOGIN")
    body = ByteWriter()
    body.write_i32(555)
    await conn.send_packet(packet_id, body.getvalue())

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SET_HEALTH")
    body = ByteWriter()
    body.write_f32(0.0)
    body.write_varint(20)
    body.write_f32(0.0)
    await conn.send_packet(packet_id, body.getvalue())

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PLAYER_COMBAT_KILL")
    body = ByteWriter()
    body.write_varint(555)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["first_reply_packet_name"] = name

    # Nothing else should arrive before the fake server hangs up -- if a
    # second PERFORM_RESPAWN were sent, this read would return it instead
    # of hitting EOF.
    try:
        await asyncio.wait_for(conn.read_packet(), timeout=0.2)
        results["got_second_packet"] = True
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        results["got_second_packet"] = False

    writer.close()


@pytest.mark.asyncio
async def test_play_loop_does_not_double_respawn_for_one_death():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_double_death_signal_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    registry = CommandRegistry()
    tracker = EntityTracker()
    heightmaps = ChunkHeightmapCache()
    blocks = ChunkBlockCache()
    movement = MovementController(tracker, blocks)

    async with server:
        conn = await Connection.open(host, port)

        try:
            await asyncio.wait_for(
                run_play_loop(conn, registry, movement, tracker, heightmaps, blocks), timeout=1.0
            )
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass

        await conn.close()

    assert results["first_reply_packet_name"] == "SERVERBOUND_CLIENT_COMMAND"
    assert results["got_second_packet"] is False
