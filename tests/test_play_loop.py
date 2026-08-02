import asyncio

import pytest

from minebot.bot.play_loop import run_play_loop
from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
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

    async def ping_handler(conn):
        calls.append("ping")
        from minebot.protocol.chat import send_say

        await send_say(conn, "pong")

    async def handler(reader, writer):
        await _fake_play_server(reader, writer, results)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    registry = CommandRegistry()
    registry.register("ping", ping_handler)

    async with server:
        conn = await Connection.open(host, port)

        try:
            await asyncio.wait_for(run_play_loop(conn, registry), timeout=1.0)
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass

        await conn.close()

    assert results["keepalive_echo"] == 99
    assert calls == ["ping"]
    assert results["reply_packet_name"] == "SERVERBOUND_CHAT_COMMAND"
    assert results["reply_text"] == "say pong"
