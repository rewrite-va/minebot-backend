"""End-to-end test of the online-mode LOGIN encryption handshake: a fake
server sends ClientboundHello with a real (test-generated) RSA-1024
keypair, expects a correctly RSA-encrypted ServerboundKeyPacket back, then
switches both directions to AES/CFB8 and continues the rest of login
encrypted -- exercising the exact cipher wiring perform_login/Connection
use in production, not just the crypto primitives in isolation
(test_encryption.py covers those).
"""

import asyncio
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import PublicFormat, Encoding

from minebot.auth.base import GameProfile
from minebot.auth.encryption import compute_server_hash, make_cfb8_cipher
from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.login_flow import perform_login
from minebot.protocol.registry import REGISTRY

STATE = "login"


class FakeOnlineAuthenticator:
    """Stands in for MicrosoftAuthenticator: returns a fixed profile and
    records the server hash it's asked to "join" with, without making any
    real network calls.
    """

    def __init__(self, username: str, profile_id: uuid.UUID):
        self.username = username
        self.profile_id = profile_id
        self.joined_with_hash: str | None = None

    async def get_profile(self) -> GameProfile:
        return GameProfile(username=self.username, profile_id=self.profile_id, access_token="fake-token")

    async def join_server(self, server_id_hash: str) -> None:
        self.joined_with_hash = server_id_hash


async def _fake_online_server(reader, writer, server_state: dict, private_key, public_key_der: bytes) -> None:
    conn = Connection(reader, writer)

    await conn.read_packet()  # handshake

    hello_raw = await conn.read_packet()
    hello_reader = ByteReader(hello_raw.data)
    name = hello_reader.read_utf()
    profile_id = hello_reader.read_uuid()

    server_id = "aaaaaaaaaaaaaaaaaaaa"  # 20 chars, matches vanilla's serverId length
    challenge = b"\x01\x02\x03\x04"

    body = ByteWriter()
    body.write_utf(server_id)
    body.write_byte_array(public_key_der)
    body.write_byte_array(challenge)
    body.write_bool(True)  # shouldAuthenticate
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_HELLO")
    await conn.send_packet(packet_id, body.getvalue())

    key_raw = await conn.read_packet()
    key_reader = ByteReader(key_raw.data)
    encrypted_secret = key_reader.read_byte_array()
    encrypted_challenge = key_reader.read_byte_array()

    shared_secret = private_key.decrypt(encrypted_secret, padding.PKCS1v15())
    decrypted_challenge = private_key.decrypt(encrypted_challenge, padding.PKCS1v15())
    assert decrypted_challenge == challenge

    server_state["expected_hash"] = compute_server_hash(server_id, shared_secret, public_key_der)

    # From here on, both directions are encrypted.
    server_encryptor, server_decryptor = make_cfb8_cipher(shared_secret)
    conn.set_encryption(server_decryptor, server_encryptor)

    # CLIENTBOUND_LOGIN_FINISHED, sent encrypted.
    body = ByteWriter()
    body.write_uuid(profile_id)
    body.write_utf(name)
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LOGIN_FINISHED")
    await conn.send_packet(packet_id, body.getvalue())

    # Client's SERVERBOUND_LOGIN_ACKNOWLEDGED, also encrypted -- proves the
    # client switched ciphers at the right point too.
    ack_raw = await conn.read_packet()
    server_state["ack_packet_name"] = REGISTRY.name_for(STATE, "serverbound", ack_raw.packet_id)

    writer.close()


@pytest.mark.asyncio
async def test_perform_login_completes_online_mode_encryption_handshake():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public_key_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)

    server_state: dict = {}

    async def handler(reader, writer):
        await _fake_online_server(reader, writer, server_state, private_key, public_key_der)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    profile_id = uuid.uuid4()
    authenticator = FakeOnlineAuthenticator("OnlineTestBot", profile_id)

    async with server:
        conn = await Connection.open(host, port)

        result = await perform_login(conn, host, port, authenticator)

        await conn.close()

    assert result.username == "OnlineTestBot"
    assert result.profile_id == profile_id
    assert authenticator.joined_with_hash == server_state["expected_hash"]
    assert server_state["ack_packet_name"] == "SERVERBOUND_LOGIN_ACKNOWLEDGED"
