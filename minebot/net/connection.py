"""Asyncio TCP connection implementing Minecraft's packet framing.

Pipeline mirrors net.minecraft.network.Connection's netty pipeline (see
FINDINGS.md): a length-prefixed frame, optionally zlib-compressed above a
threshold, optionally AES/CFB8-encrypted once login completes the encryption
handshake. Encryption is not implemented yet (auth is stubbed, see
minebot/auth/); the hooks are here so wiring it in later doesn't require
touching the framing logic.
"""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass, field

from minebot.net.types import ByteReader, ByteWriter, BufferUnderrun, decode_varint, encode_varint

# States mirror net.minecraft.network.ConnectionProtocol exactly, lowercase
# to match the keys used in packets_775.json.
STATE_HANDSHAKING = "handshake"
STATE_STATUS = "status"
STATE_LOGIN = "login"
STATE_CONFIGURATION = "configuration"
STATE_PLAY = "play"


@dataclass
class RawPacket:
    packet_id: int
    data: bytes  # payload after the packet-id varint


class Connection:
    """Owns the socket and does framing/compression only.

    Packet ID <-> meaning and field (de)serialization live one layer up
    (minebot/protocol); this class only knows how to turn a byte payload
    into a length-prefixed frame on the wire and back.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._compression_threshold = -1  # -1 = compression not enabled
        self._recv_buffer = bytearray()
        # Encryption hooks: once auth wires this up, set both to AES/CFB8
        # cipher objects and encrypt/decrypt every raw byte at the socket
        # boundary, before/after the compression layer, matching
        # Connection.setEncryptionKey's pipeline ordering (splitter/prepender
        # outermost, decrypt/encrypt just inside them).
        self._decryptor = None
        self._encryptor = None

    @classmethod
    async def open(cls, host: str, port: int) -> "Connection":
        reader, writer = await asyncio.open_connection(host, port)
        return cls(reader, writer)

    def enable_compression(self, threshold: int) -> None:
        self._compression_threshold = threshold

    def set_encryption(self, decryptor, encryptor) -> None:
        self._decryptor = decryptor
        self._encryptor = encryptor

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except ConnectionError:
            pass

    async def send_packet(self, packet_id: int, payload: bytes) -> None:
        body = ByteWriter()
        body.write_varint(packet_id)
        body.write(payload)
        frame = body.getvalue()

        if self._compression_threshold >= 0:
            frame = self._frame_compressed(frame)

        length_prefixed = encode_varint(len(frame)) + frame

        if self._encryptor is not None:
            length_prefixed = self._encryptor.update(length_prefixed)

        self._writer.write(length_prefixed)
        await self._writer.drain()

    def _frame_compressed(self, uncompressed_body: bytes) -> bytes:
        # net.minecraft.network.CompressionEncoder: below threshold, data
        # length is written as 0 (uncompressed) followed by the raw body;
        # at/above threshold, data length is the uncompressed size followed
        # by the zlib-compressed body.
        writer = ByteWriter()
        if len(uncompressed_body) < self._compression_threshold:
            writer.write_varint(0)
            writer.write(uncompressed_body)
        else:
            writer.write_varint(len(uncompressed_body))
            writer.write(zlib.compress(uncompressed_body))
        return writer.getvalue()

    async def read_packet(self) -> RawPacket:
        frame = await self._read_frame()

        if self._compression_threshold >= 0:
            frame = self._unframe_compressed(frame)

        reader = ByteReader(frame)
        packet_id = reader.read_varint()
        return RawPacket(packet_id=packet_id, data=frame[reader.pos :])

    def _unframe_compressed(self, frame: bytes) -> bytes:
        reader = ByteReader(frame)
        data_length = reader.read_varint()
        rest = frame[reader.pos :]
        if data_length == 0:
            return rest
        return zlib.decompress(rest)

    async def _read_frame(self) -> bytes:
        length = await self._read_varint_from_stream()
        payload = await self._read_exact(length)
        return payload

    async def _read_varint_from_stream(self) -> int:
        buf = bytearray()
        while True:
            byte = await self._read_exact(1)
            buf.append(byte[0])
            try:
                value, consumed = decode_varint(bytes(buf))
            except BufferUnderrun:
                if len(buf) >= 5:
                    raise ValueError("varint too long reading frame length")
                continue
            return value

    async def _read_exact(self, n: int) -> bytes:
        raw = await self._reader.readexactly(n)
        if self._decryptor is not None:
            raw = self._decryptor.update(raw)
        return raw
