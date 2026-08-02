"""Minimal network-NBT reader: only what's needed to pull plain text out of
chat Component tags (see minebot/protocol/chat.py).

Network NBT (used for packet payloads since the 1.20.2 protocol rework, per
NbtIo.readAnyTag/writeAnyTag in the decompiled 26.1.2 source) differs from
file NBT: no root name string, just a type byte followed directly by the
tag's payload. Within compounds/lists, named/typed entries still use the
classic NBT binary layout (type byte, name for compound entries, payload).

This is NOT a general-purpose NBT library: TAG_Byte_Array, TAG_Int_Array,
and TAG_Long_Array are parsed only far enough to skip over them correctly
(their contents aren't needed for chat text extraction). If a future
PLAY-phase packet needs full NBT (e.g. item components, block entity data),
extend this rather than writing a second parser.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


@dataclass
class NbtReader:
    data: bytes
    pos: int = 0

    def _read(self, n: int) -> bytes:
        out = self.data[self.pos : self.pos + n]
        if len(out) != n:
            raise ValueError("truncated NBT data")
        self.pos += n
        return out

    def read_u8(self) -> int:
        return self._read(1)[0]

    def read_i8(self) -> int:
        (value,) = struct.unpack(">b", self._read(1))
        return value

    def read_i16(self) -> int:
        (value,) = struct.unpack(">h", self._read(2))
        return value

    def read_i32(self) -> int:
        (value,) = struct.unpack(">i", self._read(4))
        return value

    def read_i64(self) -> int:
        (value,) = struct.unpack(">q", self._read(8))
        return value

    def read_f32(self) -> float:
        (value,) = struct.unpack(">f", self._read(4))
        return value

    def read_f64(self) -> float:
        (value,) = struct.unpack(">d", self._read(8))
        return value

    def read_modified_utf8(self) -> str:
        # Java's DataOutput#writeUTF length-prefixes with an unsigned short.
        # Ordinary text is valid UTF-8 too; not implementing full CESU-8
        # edge cases (lone surrogate pairs) since chat text won't hit them.
        length = struct.unpack(">H", self._read(2))[0]
        return self._read(length).decode("utf-8", errors="replace")

    def read_payload(self, tag_type: int) -> Any:
        if tag_type == TAG_BYTE:
            return self.read_i8()
        if tag_type == TAG_SHORT:
            return self.read_i16()
        if tag_type == TAG_INT:
            return self.read_i32()
        if tag_type == TAG_LONG:
            return self.read_i64()
        if tag_type == TAG_FLOAT:
            return self.read_f32()
        if tag_type == TAG_DOUBLE:
            return self.read_f64()
        if tag_type == TAG_STRING:
            return self.read_modified_utf8()
        if tag_type == TAG_BYTE_ARRAY:
            length = self.read_i32()
            return list(self._read(length))
        if tag_type == TAG_INT_ARRAY:
            length = self.read_i32()
            return [self.read_i32() for _ in range(length)]
        if tag_type == TAG_LONG_ARRAY:
            length = self.read_i32()
            return [self.read_i64() for _ in range(length)]
        if tag_type == TAG_LIST:
            element_type = self.read_u8()
            length = self.read_i32()
            return [self.read_payload(element_type) for _ in range(length)]
        if tag_type == TAG_COMPOUND:
            result: dict[str, Any] = {}
            while True:
                child_type = self.read_u8()
                if child_type == TAG_END:
                    return result
                name = self.read_modified_utf8()
                result[name] = self.read_payload(child_type)
        raise ValueError(f"unsupported NBT tag type {tag_type}")


def read_network_tag(data: bytes, offset: int = 0) -> tuple[Any, int]:
    """Reads one NbtIo.readAnyTag-framed tag (type byte + payload, no name)
    starting at `offset`. Returns (value, new_offset). value is None for
    TAG_End (the NBT equivalent of "absent"/null, as returned by
    FriendlyByteBuf.readNbt when the client sends nothing).
    """
    reader = NbtReader(data, offset)
    tag_type = reader.read_u8()
    if tag_type == TAG_END:
        return None, reader.pos
    value = reader.read_payload(tag_type)
    return value, reader.pos
