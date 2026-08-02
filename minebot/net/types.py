"""Primitive wire-format encode/decode helpers shared by every packet.

Field shapes here (VarInt, length-prefixed UTF-8 strings, UUID-as-two-longs)
are unchanged from the protocol's earliest versions and confirmed still in
use in 26.1.2 by reading the decompiled FriendlyByteBuf-based packet records
(see FINDINGS.md).
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

MAX_VARINT_SIZE = 5  # ceil(32 / 7)


class BufferUnderrun(Exception):
    pass


def encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 32
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    """Returns (value, bytes_consumed). Raises BufferUnderrun if incomplete."""
    result = 0
    for i in range(MAX_VARINT_SIZE):
        if offset + i >= len(buf):
            raise BufferUnderrun("incomplete varint")
        byte = buf[offset + i]
        result |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            if result >= 1 << 31:
                result -= 1 << 32
            return result, i + 1
    raise ValueError("varint too big")


@dataclass
class ByteReader:
    data: bytes
    pos: int = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise BufferUnderrun(f"wanted {n} bytes, have {self.remaining()}")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def read_varint(self) -> int:
        value, consumed = decode_varint(self.data, self.pos)
        self.pos += consumed
        return value

    def read_bool(self) -> bool:
        return self.read(1)[0] != 0

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u16(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def read_i32(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def read_i64(self) -> int:
        return struct.unpack(">q", self.read(8))[0]

    def read_f32(self) -> float:
        return struct.unpack(">f", self.read(4))[0]

    def read_f64(self) -> float:
        return struct.unpack(">d", self.read(8))[0]

    def read_utf(self) -> str:
        length = self.read_varint()
        return self.read(length).decode("utf-8")

    def read_uuid(self) -> uuid.UUID:
        most, least = struct.unpack(">qq", self.read(16))
        return uuid.UUID(int=((most & 0xFFFFFFFFFFFFFFFF) << 64) | (least & 0xFFFFFFFFFFFFFFFF))

    def read_byte_array(self) -> bytes:
        length = self.read_varint()
        return self.read(length)


class ByteWriter:
    def __init__(self) -> None:
        self._buf = bytearray()

    def getvalue(self) -> bytes:
        return bytes(self._buf)

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    def write_varint(self, value: int) -> None:
        self._buf.extend(encode_varint(value))

    def write_bool(self, value: bool) -> None:
        self._buf.append(1 if value else 0)

    def write_u8(self, value: int) -> None:
        self._buf.append(value & 0xFF)

    def write_u16(self, value: int) -> None:
        self._buf.extend(struct.pack(">H", value))

    def write_i32(self, value: int) -> None:
        self._buf.extend(struct.pack(">i", value))

    def write_i64(self, value: int) -> None:
        self._buf.extend(struct.pack(">q", value))

    def write_f32(self, value: float) -> None:
        self._buf.extend(struct.pack(">f", value))

    def write_f64(self, value: float) -> None:
        self._buf.extend(struct.pack(">d", value))

    def write_utf(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self.write_varint(len(encoded))
        self.write(encoded)

    def write_uuid(self, value: uuid.UUID) -> None:
        as_int = value.int
        most = (as_int >> 64) & 0xFFFFFFFFFFFFFFFF
        least = as_int & 0xFFFFFFFFFFFFFFFF
        if most >= 1 << 63:
            most -= 1 << 64
        if least >= 1 << 63:
            least -= 1 << 64
        self._buf.extend(struct.pack(">qq", most, least))

    def write_byte_array(self, value: bytes) -> None:
        self.write_varint(len(value))
        self.write(value)
