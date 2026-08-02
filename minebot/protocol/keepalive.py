"""Keepalive + ping/pong: the server periodically sends these and expects a
prompt reply, or it disconnects the client for timing out. Both
CONFIGURATION and PLAY states have their own KeepAlive/Ping packets (same
field shapes, different packet ids per state -- see packets_775.json), so
every function here takes `state` explicitly rather than hardcoding one.

Discovered the hard way: our first real end-to-end run against an
online-mode server got through LOGIN and into CONFIGURATION correctly, then
silently died a few seconds later. Server logs showed nothing wrong on its
end; our own logs showed we received CLIENTBOUND_PING and
CLIENTBOUND_KEEP_ALIVE during CONFIGURATION and just ignored both (this
handling previously only existed for the PLAY state) -- the server then
dropped us for not responding. CONFIGURATION-phase keepalive/ping handling
is not optional in practice, even though it's easy to overlook since
CONFIGURATION is usually thought of as a short, one-shot negotiation phase.

Field layout: KeepAlive carries an i64 id (echoed back verbatim). Ping/Pong
carry a plain i32 id (not a VarInt) -- see decompiled ClientboundPingPacket/
ServerboundPongPacket.
"""

from __future__ import annotations

from minebot.net.connection import Connection
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.registry import REGISTRY, Direction


def parse_keepalive(state: str, packet_id: int, data: bytes) -> int | None:
    name = REGISTRY.name_for(state, "clientbound", packet_id)
    if name != "CLIENTBOUND_KEEP_ALIVE":
        return None
    return ByteReader(data).read_i64()


async def send_keepalive_response(conn: Connection, state: str, keepalive_id: int) -> None:
    writer = ByteWriter()
    writer.write_i64(keepalive_id)
    packet_id = REGISTRY.id_for(state, "serverbound", "SERVERBOUND_KEEP_ALIVE")
    await conn.send_packet(packet_id, writer.getvalue())


def parse_ping(state: str, packet_id: int, data: bytes) -> int | None:
    name = REGISTRY.name_for(state, "clientbound", packet_id)
    if name != "CLIENTBOUND_PING":
        return None
    return ByteReader(data).read_i32()


async def send_pong(conn: Connection, state: str, ping_id: int) -> None:
    writer = ByteWriter()
    writer.write_i32(ping_id)
    packet_id = REGISTRY.id_for(state, "serverbound", "SERVERBOUND_PONG")
    await conn.send_packet(packet_id, writer.getvalue())
