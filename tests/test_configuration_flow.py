"""Exercises run_configuration_phase against a minimal fake server that
sends a known-packs request, a code-of-conduct prompt, then finishes
configuration -- confirming the client replies with an empty known-packs
list, an accept, and the finish acknowledgement, in that order.
"""

import asyncio

import pytest

from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.configuration import run_configuration_phase
from minebot.protocol.registry import REGISTRY

STATE = "configuration"


async def _fake_configuration_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    conn = Connection(reader, writer)
    received: list[str] = []

    # ServerboundClientInformation (sent unconditionally on phase entry)
    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    received.append(name)
    assert name == "SERVERBOUND_CLIENT_INFORMATION"

    # Interleave a keepalive and a ping before continuing -- regression
    # coverage for a real bug: our first live run against an online-mode
    # server got disconnected because CONFIGURATION-phase keepalive/ping
    # were only wired up for PLAY. The server sends both during
    # CONFIGURATION and disconnects a client that doesn't answer.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    body = ByteWriter()
    body.write_i64(555)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    assert name == "SERVERBOUND_KEEP_ALIVE"
    assert ByteReader(raw.data).read_i64() == 555

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_PING")
    body = ByteWriter()
    body.write_i32(9)
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    assert name == "SERVERBOUND_PONG"
    assert ByteReader(raw.data).read_i32() == 9

    # Ask for known packs; expect an empty list back.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SELECT_KNOWN_PACKS")
    body = ByteWriter()
    body.write_varint(0)  # zero known packs offered by the server, for simplicity
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    received.append(name)
    assert name == "SERVERBOUND_SELECT_KNOWN_PACKS"
    reply_reader = ByteReader(raw.data)
    assert reply_reader.read_varint() == 0  # client replied with an empty list too

    # Code of conduct prompt; expect an accept back.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_CODE_OF_CONDUCT")
    body = ByteWriter()
    body.write_utf("Be nice.")
    await conn.send_packet(packet_id, body.getvalue())

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    received.append(name)
    assert name == "SERVERBOUND_ACCEPT_CODE_OF_CONDUCT"

    # Finish configuration; expect the client's finish ack back.
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_FINISH_CONFIGURATION")
    await conn.send_packet(packet_id, b"")

    raw = await conn.read_packet()
    name = REGISTRY.name_for(STATE, "serverbound", raw.packet_id)
    received.append(name)
    assert name == "SERVERBOUND_FINISH_CONFIGURATION"

    writer.close()

    server_conn_state["received"] = received


server_conn_state: dict = {}


@pytest.mark.asyncio
async def test_run_configuration_phase_completes_full_handshake():
    server = await asyncio.start_server(_fake_configuration_server, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)

        await run_configuration_phase(conn)

        await conn.close()

    assert server_conn_state["received"] == [
        "SERVERBOUND_CLIENT_INFORMATION",
        "SERVERBOUND_SELECT_KNOWN_PACKS",
        "SERVERBOUND_ACCEPT_CODE_OF_CONDUCT",
        "SERVERBOUND_FINISH_CONFIGURATION",
    ]
