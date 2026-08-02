"""CONFIGURATION-phase packet (de)serialization and the driver that walks
the phase to completion.

Field layouts read off the decompiled 26.1.2 source: ServerboundClientInformationPacket
/ ClientInformation, ClientboundSelectKnownPacks / ServerboundSelectKnownPacks /
KnownPack, ClientboundCodeOfConductPacket / ServerboundAcceptCodeOfConductPacket,
ClientboundFinishConfigurationPacket / ServerboundFinishConfigurationPacket.
See FINDINGS.md for the full packet inventory of this phase.

We don't implement most of this phase's packets (cookies, resource packs,
custom payload, registry data, feature flags, tags, dialogs, server links) —
they're drained and ignored, which is safe: the client isn't required to
act on them to proceed, only to acknowledge CLIENTBOUND_FINISH_CONFIGURATION
and (new in this version) accept the code of conduct if the server sends one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from minebot.net.connection import Connection, STATE_CONFIGURATION
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.keepalive import parse_keepalive, parse_ping, send_keepalive_response, send_pong
from minebot.protocol.registry import REGISTRY

STATE = STATE_CONFIGURATION
log = logging.getLogger("minebot.configuration")


@dataclass(frozen=True)
class KnownPack:
    namespace: str
    id: str
    version: str


def _read_known_pack(reader: ByteReader) -> KnownPack:
    return KnownPack(reader.read_utf(), reader.read_utf(), reader.read_utf())


def _write_known_pack(writer: ByteWriter, pack: KnownPack) -> None:
    writer.write_utf(pack.namespace)
    writer.write_utf(pack.id)
    writer.write_utf(pack.version)


@dataclass
class SelectKnownPacks:
    known_packs: list[KnownPack]


@dataclass
class CodeOfConduct:
    text: str


@dataclass
class ConfigurationFinished:
    pass


@dataclass
class ConfigurationDisconnect:
    reason: str


def parse_configuration_clientbound(packet_id: int, data: bytes):
    """Returns a decoded dataclass for packets we act on, or None for
    anything we intentionally drain-and-ignore (see module docstring).
    """
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    reader = ByteReader(data)

    if name == "CLIENTBOUND_SELECT_KNOWN_PACKS":
        count = reader.read_varint()
        packs = [_read_known_pack(reader) for _ in range(count)]
        return SelectKnownPacks(packs)

    if name == "CLIENTBOUND_CODE_OF_CONDUCT":
        return CodeOfConduct(reader.read_utf())

    if name == "CLIENTBOUND_FINISH_CONFIGURATION":
        return ConfigurationFinished()

    if name == "CLIENTBOUND_DISCONNECT":
        return ConfigurationDisconnect(reader.read_utf())

    return None


async def send_client_information(conn: Connection) -> None:
    # Mirrors ClientInformation.createDefault(): en_us, view distance 2,
    # full chat visibility, colors on, no skin-part customisation, right
    # hand, no text filtering, allow listing, all particles. Enum fields
    # encode as their ordinal (writeEnum/readEnum use a VarInt of the enum's
    # declared order).
    CHAT_VISIBILITY_FULL = 0
    MAIN_HAND_RIGHT = 1
    PARTICLE_STATUS_ALL = 0

    writer = ByteWriter()
    writer.write_utf("en_us")
    writer.write_u8(2)  # view distance (signed byte on the wire; small positive value)
    writer.write_varint(CHAT_VISIBILITY_FULL)
    writer.write_bool(True)  # chat colors
    writer.write_u8(0)  # model customisation bitmask
    writer.write_varint(MAIN_HAND_RIGHT)
    writer.write_bool(False)  # text filtering
    writer.write_bool(True)  # allow listing
    writer.write_varint(PARTICLE_STATUS_ALL)

    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_CLIENT_INFORMATION")
    await conn.send_packet(packet_id, writer.getvalue())


async def send_known_packs(conn: Connection, known_packs: list[KnownPack]) -> None:
    writer = ByteWriter()
    writer.write_varint(len(known_packs))
    for pack in known_packs:
        _write_known_pack(writer, pack)
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_SELECT_KNOWN_PACKS")
    await conn.send_packet(packet_id, writer.getvalue())


async def send_accept_code_of_conduct(conn: Connection) -> None:
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_ACCEPT_CODE_OF_CONDUCT")
    await conn.send_packet(packet_id, b"")


async def send_finish_configuration(conn: Connection) -> None:
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_FINISH_CONFIGURATION")
    await conn.send_packet(packet_id, b"")


class ConfigurationError(Exception):
    pass


async def run_configuration_phase(conn: Connection) -> None:
    """Drives CONFIGURATION to completion. Call immediately after
    perform_login() returns. Leaves the connection ready for the PLAY phase
    (caller still needs to send SERVERBOUND_FINISH_CONFIGURATION's
    corresponding state switch on the receiving side — i.e. start parsing
    subsequent packets as PLAY-state packets once this returns).
    """
    await send_client_information(conn)

    while True:
        raw = await conn.read_packet()
        name = REGISTRY.name_for(STATE, "clientbound", raw.packet_id)
        log.debug("got CONFIGURATION packet id=%d name=%s data_len=%d", raw.packet_id, name, len(raw.data))

        keepalive_id = parse_keepalive(STATE, raw.packet_id, raw.data)
        if keepalive_id is not None:
            await send_keepalive_response(conn, STATE, keepalive_id)
            continue

        ping_id = parse_ping(STATE, raw.packet_id, raw.data)
        if ping_id is not None:
            await send_pong(conn, STATE, ping_id)
            continue

        parsed = parse_configuration_clientbound(raw.packet_id, raw.data)

        if isinstance(parsed, ConfigurationDisconnect):
            raise ConfigurationError(f"disconnected during configuration: {parsed.reason}")

        if isinstance(parsed, SelectKnownPacks):
            # We know none of the server's packs (no resource/data packs
            # bundled with this bot); replying with an empty list is the
            # documented way to say "send me everything as raw registry
            # data instead of assuming I already have it".
            await send_known_packs(conn, [])
            continue

        if isinstance(parsed, CodeOfConduct):
            # New in this version (see FINDINGS.md). No interactive user to
            # ask, so auto-accept; a real deployment may want to log
            # parsed.text somewhere for a human to review instead.
            await send_accept_code_of_conduct(conn)
            continue

        if isinstance(parsed, ConfigurationFinished):
            await send_finish_configuration(conn)
            return

        # Unhandled: cookies, resource pack push, registry data, feature
        # flags, tags, custom payload, dialogs, server links. Safe to
        # ignore for the MVP — see module docstring.
