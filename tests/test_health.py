import asyncio

import pytest

from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.health import (
    ClientCommandAction,
    OwnEntityId,
    PlayerCombatKill,
    SetHealth,
    parse_own_entity_id,
    parse_player_combat_kill,
    parse_respawn,
    parse_set_health,
    send_perform_respawn,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def test_parse_own_entity_id():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LOGIN")
    writer = ByteWriter()
    writer.write_i32(12345)
    # Real CLIENTBOUND_LOGIN has many more fields after playerId; we only
    # ever read the first one, so leaving the rest empty is fine.
    assert parse_own_entity_id(packet_id, writer.getvalue()) == OwnEntityId(entity_id=12345)


def test_parse_own_entity_id_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_own_entity_id(packet_id, b"\x00" * 8) is None


def test_parse_set_health():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SET_HEALTH")
    writer = ByteWriter()
    writer.write_f32(0.0)
    writer.write_varint(20)
    writer.write_f32(5.0)
    result = parse_set_health(packet_id, writer.getvalue())
    assert result == SetHealth(health=0.0, food=20, saturation=5.0)


def test_parse_player_combat_kill():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PLAYER_COMBAT_KILL")
    writer = ByteWriter()
    writer.write_varint(42)
    # Real packet has a Component message after this; not needed/parsed.
    result = parse_player_combat_kill(packet_id, writer.getvalue())
    assert result == PlayerCombatKill(player_id=42)


def test_parse_respawn():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_RESPAWN")
    assert parse_respawn(packet_id, b"") is not None


def test_parse_respawn_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_respawn(packet_id, b"\x00" * 8) is None


async def _fake_respawn_server(reader, writer, results: dict) -> None:
    conn = Connection(reader, writer)
    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["packet_name"] = name
    results["action"] = ByteReader(raw.data).read_varint()
    writer.close()


@pytest.mark.asyncio
async def test_send_perform_respawn_roundtrip():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_respawn_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        await send_perform_respawn(conn)
        await conn.close()
        await asyncio.sleep(0.05)

    assert results["packet_name"] == "SERVERBOUND_CLIENT_COMMAND"
    assert results["action"] == int(ClientCommandAction.PERFORM_RESPAWN)
