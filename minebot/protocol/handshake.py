"""Handshake and login phase packet (de)serialization.

Field layouts read directly off the decompiled 26.1.2 source
(ClientIntentionPacket, ServerboundHelloPacket, ClientboundLoginCompressionPacket,
ClientboundHelloPacket, ServerboundKeyPacket) — see FINDINGS.md. Unchanged
from the shape used since the protocol's earliest online-mode-capable
versions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import IntEnum

from minebot.net.connection import Connection, STATE_HANDSHAKING, STATE_LOGIN
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.registry import REGISTRY


class ClientIntent(IntEnum):
    STATUS = 1
    LOGIN = 2
    TRANSFER = 3


async def send_handshake(conn: Connection, protocol_version: int, host: str, port: int, intent: ClientIntent) -> None:
    writer = ByteWriter()
    writer.write_varint(protocol_version)
    writer.write_utf(host)
    writer.write_u16(port)
    writer.write_varint(int(intent))
    packet_id = REGISTRY.id_for(STATE_HANDSHAKING, "serverbound", "CLIENT_INTENTION")
    await conn.send_packet(packet_id, writer.getvalue())


async def send_hello(conn: Connection, name: str, profile_id: uuid.UUID) -> None:
    writer = ByteWriter()
    writer.write_utf(name)
    writer.write_uuid(profile_id)
    packet_id = REGISTRY.id_for(STATE_LOGIN, "serverbound", "SERVERBOUND_HELLO")
    await conn.send_packet(packet_id, writer.getvalue())


@dataclass
class ClientboundHello:
    server_id: str
    public_key: bytes
    challenge: bytes
    should_authenticate: bool


@dataclass
class LoginCompression:
    threshold: int


@dataclass
class LoginFinished:
    profile_id: uuid.UUID
    username: str


@dataclass
class LoginDisconnect:
    reason: str


def parse_login_clientbound(packet_id: int, data: bytes):
    """Returns a decoded dataclass for a known LOGIN-state clientbound
    packet, or None if we don't have a parser for it yet (e.g. custom query,
    cookie request — not needed for the MVP login path).
    """
    name = REGISTRY.name_for(STATE_LOGIN, "clientbound", packet_id)
    reader = ByteReader(data)

    if name == "CLIENTBOUND_HELLO":
        server_id = reader.read_utf()
        public_key = reader.read_byte_array()
        challenge = reader.read_byte_array()
        should_authenticate = reader.read_bool()
        return ClientboundHello(server_id, public_key, challenge, should_authenticate)

    if name == "CLIENTBOUND_LOGIN_COMPRESSION":
        return LoginCompression(reader.read_varint())

    if name == "CLIENTBOUND_LOGIN_FINISHED":
        profile_id = reader.read_uuid()
        username = reader.read_utf()
        return LoginFinished(profile_id, username)

    if name == "CLIENTBOUND_LOGIN_DISCONNECT":
        return LoginDisconnect(reader.read_utf())

    return None


async def send_login_acknowledged(conn: Connection) -> None:
    packet_id = REGISTRY.id_for(STATE_LOGIN, "serverbound", "SERVERBOUND_LOGIN_ACKNOWLEDGED")
    await conn.send_packet(packet_id, b"")


async def send_key(conn: Connection, encrypted_shared_secret: bytes, encrypted_challenge: bytes) -> None:
    writer = ByteWriter()
    writer.write_byte_array(encrypted_shared_secret)
    writer.write_byte_array(encrypted_challenge)
    packet_id = REGISTRY.id_for(STATE_LOGIN, "serverbound", "SERVERBOUND_KEY")
    await conn.send_packet(packet_id, writer.getvalue())
