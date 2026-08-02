import asyncio
import math

import pytest

from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.movement import (
    PlayerPositionSync,
    REL_X,
    REL_Y,
    REL_Y_ROT,
    REL_Z,
    parse_player_position,
    send_accept_teleportation,
    send_move_player_pos_rot,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def _encode_player_position(teleport_id: int, x: float, y: float, z: float, yaw: float, pitch: float, relatives: int) -> bytes:
    writer = ByteWriter()
    writer.write_varint(teleport_id)
    writer.write_f64(x)
    writer.write_f64(y)
    writer.write_f64(z)
    writer.write_f64(0.0)  # deltaMovement.x
    writer.write_f64(0.0)  # deltaMovement.y
    writer.write_f64(0.0)  # deltaMovement.z
    writer.write_f32(yaw)
    writer.write_f32(pitch)
    writer.write_i32(relatives)
    return writer.getvalue()


def test_parse_player_position_absolute():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PLAYER_POSITION")
    data = _encode_player_position(5, 10.0, 64.0, -20.0, 90.0, 0.0, 0)

    result = parse_player_position(packet_id, data)

    assert result == PlayerPositionSync(
        teleport_id=5, x=10.0, y=64.0, z=-20.0, yaw=90.0, pitch=0.0, relatives=0
    )


def test_parse_player_position_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_player_position(packet_id, b"\x00" * 8) is None


def test_parse_player_position_relative_flags_preserved():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PLAYER_POSITION")
    data = _encode_player_position(1, 1.0, 2.0, 3.0, 0.0, 0.0, REL_X | REL_Y_ROT)

    result = parse_player_position(packet_id, data)
    assert result.relatives == REL_X | REL_Y_ROT


async def _fake_teleport_ack_server(reader, writer, results: dict) -> None:
    conn = Connection(reader, writer)
    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["packet_name"] = name
    results["teleport_id"] = ByteReader(raw.data).read_varint()
    writer.close()


@pytest.mark.asyncio
async def test_send_accept_teleportation_roundtrip():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_teleport_ack_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        await send_accept_teleportation(conn, 42)
        await conn.close()
        await asyncio.sleep(0.05)

    assert results["packet_name"] == "SERVERBOUND_ACCEPT_TELEPORTATION"
    assert results["teleport_id"] == 42


async def _fake_move_server(reader, writer, results: dict) -> None:
    conn = Connection(reader, writer)
    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    results["packet_name"] = name
    reader_ = ByteReader(raw.data)
    results["x"] = reader_.read_f64()
    results["y"] = reader_.read_f64()
    results["z"] = reader_.read_f64()
    results["yaw"] = reader_.read_f32()
    results["pitch"] = reader_.read_f32()
    results["flags"] = reader_.read_u8()
    writer.close()


@pytest.mark.asyncio
async def test_send_move_player_pos_rot_roundtrip():
    results: dict = {}

    async def handler(reader, writer):
        await _fake_move_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        await send_move_player_pos_rot(conn, 1.5, 64.0, -2.5, 90.0, 10.0, on_ground=True)
        await conn.close()
        await asyncio.sleep(0.05)

    assert results["packet_name"] == "SERVERBOUND_MOVE_PLAYER_POS_ROT"
    assert results["x"] == pytest.approx(1.5)
    assert results["y"] == pytest.approx(64.0)
    assert results["z"] == pytest.approx(-2.5)
    assert results["yaw"] == pytest.approx(90.0)
    assert results["pitch"] == pytest.approx(10.0)
    assert results["flags"] == 1
