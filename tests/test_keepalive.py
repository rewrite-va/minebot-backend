import asyncio

import pytest

from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.keepalive import parse_keepalive, parse_ping, send_keepalive_response, send_pong
from minebot.protocol.registry import REGISTRY

STATE = "play"


def test_parse_keepalive_extracts_id():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    writer = ByteWriter()
    writer.write_i64(123456789)
    assert parse_keepalive(STATE, packet_id, writer.getvalue()) == 123456789


def test_parse_keepalive_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SYSTEM_CHAT")
    assert parse_keepalive(STATE, packet_id, b"") is None


def test_parse_ping_extracts_id():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PING")
    writer = ByteWriter()
    writer.write_i32(7)
    assert parse_ping(STATE, packet_id, writer.getvalue()) == 7


def test_parse_ping_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SYSTEM_CHAT")
    assert parse_ping(STATE, packet_id, b"") is None


def test_keepalive_and_ping_ids_differ_by_state():
    # Regression check for the bug that broke our first real online-mode
    # run: CONFIGURATION and PLAY have separate packet-id tables, so a
    # keepalive/ping parser hardcoded to one state silently never matches
    # in the other.
    config_id = REGISTRY.id_for("configuration", "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    play_id = REGISTRY.id_for("play", "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert config_id != play_id


async def _fake_keepalive_server(reader, writer, echoed: dict) -> None:
    conn = Connection(reader, writer)
    raw = await conn.read_packet()
    echoed["id"] = ByteReader(raw.data).read_i64()
    writer.close()


@pytest.mark.asyncio
async def test_send_keepalive_response_roundtrip():
    echoed: dict = {}

    async def handler(reader, writer):
        await _fake_keepalive_server(reader, writer, echoed)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        await send_keepalive_response(conn, STATE, 42)
        await conn.close()
        await asyncio.sleep(0.05)

    assert echoed["id"] == 42


async def _fake_pong_server(reader, writer, echoed: dict) -> None:
    conn = Connection(reader, writer)
    raw = await conn.read_packet()
    echoed["id"] = ByteReader(raw.data).read_i32()
    writer.close()


@pytest.mark.asyncio
async def test_send_pong_roundtrip():
    echoed: dict = {}

    async def handler(reader, writer):
        await _fake_pong_server(reader, writer, echoed)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        await send_pong(conn, STATE, 7)
        await conn.close()
        await asyncio.sleep(0.05)

    assert echoed["id"] == 7
