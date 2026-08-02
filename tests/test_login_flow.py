"""End-to-end test of the LOGIN phase against a minimal fake server that
speaks just enough of the offline-mode login sequence: read handshake, read
hello, reply login-compression then login-finished. Exercises framing,
compression toggling, and the client-side login_flow driver together.
"""

import asyncio
import uuid

import pytest

from minebot.auth.base import OfflineAuthenticator
from minebot.net.connection import Connection
from minebot.protocol.handshake import ClientboundHello, parse_login_clientbound
from minebot.protocol.login_flow import LoginError, perform_login
from minebot.protocol.registry import REGISTRY
from minebot.net.types import ByteWriter


async def _fake_offline_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    server_conn = Connection(reader, writer)

    # Handshake (no reply expected)
    await server_conn.read_packet()

    # ServerboundHello
    hello_raw = await server_conn.read_packet()
    from minebot.net.types import ByteReader

    hello_reader = ByteReader(hello_raw.data)
    name = hello_reader.read_utf()
    profile_id = hello_reader.read_uuid()

    # CLIENTBOUND_LOGIN_COMPRESSION, threshold 256
    body = ByteWriter()
    body.write_varint(256)
    packet_id = REGISTRY.id_for("login", "clientbound", "CLIENTBOUND_LOGIN_COMPRESSION")
    await server_conn.send_packet(packet_id, body.getvalue())
    server_conn.enable_compression(256)

    # CLIENTBOUND_LOGIN_FINISHED
    body = ByteWriter()
    body.write_uuid(profile_id)
    body.write_utf(name)
    packet_id = REGISTRY.id_for("login", "clientbound", "CLIENTBOUND_LOGIN_FINISHED")
    await server_conn.send_packet(packet_id, body.getvalue())

    # Client replies with SERVERBOUND_LOGIN_ACKNOWLEDGED; just drain it.
    await server_conn.read_packet()

    writer.close()


@pytest.mark.asyncio
async def test_perform_login_against_fake_offline_server():
    server = await asyncio.start_server(_fake_offline_server, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        conn = await Connection.open(host, port)
        authenticator = OfflineAuthenticator("TestBot")

        result = await perform_login(conn, host, port, authenticator)

        assert result.username == "TestBot"
        assert isinstance(result.profile_id, uuid.UUID)

        await conn.close()


def test_offline_authenticator_derives_deterministic_uuid():
    import asyncio as aio

    profile = aio.run(OfflineAuthenticator("Notch").get_profile())
    # Matches vanilla's UUID.nameUUIDFromBytes("OfflinePlayer:Notch"),
    # a well-known reference value.
    assert str(profile.profile_id) == "b50ad385-829d-3141-a216-7e7d7539ba7f"
