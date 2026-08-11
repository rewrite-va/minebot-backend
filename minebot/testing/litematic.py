"""Reads `.litematic` schematic files (Litematica mod's own save format) so
test scenarios can be built in-game with Litematica (a normal creative-mode
workflow -- place blocks by hand, `/litematic create`) and then replayed as
real `/setblock` commands against the disposable test world, without adding
Litematica itself as a mod dependency of the test client.

Deliberately a from-scratch minimal NBT + Litematica bit-packing reader, not
a wrapper around Litematica's own Java code or a Python NBT library (neither
is available/wired into this repo -- see minebot/testing/__init__.py's own
neighbors for the existing "small, self-contained, no new dependency"
pattern this follows). Decoding logic (tag types, bit-packing math) was
cross-checked directly against Litematica's own source
(`fi.dy.masa.litematica.schematic.container.LitematicaBitArray`/
`LitematicaBlockStateContainer`, `/home/colaila/git/mods/litematica`), not
guessed from the format's public docs, since the bit-packing in particular
has a non-obvious minimum (`bitsPerEntry` is `max(2, ceil(log2(paletteSize)))`,
never fewer than 2 bits even for a 2-entry palette).

Only reads BlockStatePalette + BlockStates (the block grid) -- entities,
tile-entity NBT (chest contents etc.), and pending ticks are ignored, since
nothing in this repo's test scenarios needs them yet.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass
from pathlib import Path

# NBT tag type IDs (Java edition NBT spec).
_TAG_BYTE = 1
_TAG_SHORT = 2
_TAG_INT = 3
_TAG_LONG = 4
_TAG_FLOAT = 5
_TAG_DOUBLE = 6
_TAG_BYTE_ARRAY = 7
_TAG_STRING = 8
_TAG_LIST = 9
_TAG_COMPOUND = 10
_TAG_INT_ARRAY = 11
_TAG_LONG_ARRAY = 12


class _NbtReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def _take(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def u8(self) -> int:
        return self._take(1)[0]

    def i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self._take(8))[0]

    def string(self) -> str:
        (length,) = struct.unpack(">H", self._take(2))
        return self._take(length).decode("utf-8")

    def payload(self, tag_type: int):
        if tag_type == _TAG_BYTE:
            return self.u8()
        if tag_type == _TAG_SHORT:
            return struct.unpack(">h", self._take(2))[0]
        if tag_type == _TAG_INT:
            return self.i32()
        if tag_type == _TAG_LONG:
            return self.i64()
        if tag_type == _TAG_FLOAT:
            return struct.unpack(">f", self._take(4))[0]
        if tag_type == _TAG_DOUBLE:
            return struct.unpack(">d", self._take(8))[0]
        if tag_type == _TAG_BYTE_ARRAY:
            n = self.i32()
            return list(self._take(n))
        if tag_type == _TAG_STRING:
            return self.string()
        if tag_type == _TAG_LIST:
            item_type = self.u8()
            n = self.i32()
            return [self.payload(item_type) for _ in range(n)]
        if tag_type == _TAG_COMPOUND:
            result: dict = {}
            while True:
                item_type = self.u8()
                if item_type == 0:
                    return result
                name = self.string()
                result[name] = self.payload(item_type)
        if tag_type == _TAG_INT_ARRAY:
            n = self.i32()
            return [self.i32() for _ in range(n)]
        if tag_type == _TAG_LONG_ARRAY:
            n = self.i32()
            return [self.i64() for _ in range(n)]
        raise ValueError(f"unknown NBT tag type {tag_type}")


def _read_root_compound(data: bytes) -> dict:
    reader = _NbtReader(data)
    root_type = reader.u8()
    if root_type != _TAG_COMPOUND:
        raise ValueError(f"expected a root TAG_Compound, got tag type {root_type}")
    reader.string()  # root name -- unused, litematic files always use ""
    return reader.payload(_TAG_COMPOUND)


def _bits_per_entry(palette_size: int) -> int:
    """Matches LitematicaBlockStateContainer.setBits exactly: `max(2,
    32 - numberOfLeadingZeros(paletteSize - 1))`, i.e. `max(2,
    ceil(log2(paletteSize)))` -- confirmed against the real Java source
    rather than assumed, since a naive "ceil(log2(n))" alone under-reads a
    2-entry palette (1 bit, not the real minimum of 2) and would misalign
    every subsequent entry in the packed long array.
    """
    return max(2, (palette_size - 1).bit_length())


def _unpack_block_states(long_array: list[int], bits_per_entry: int, count: int) -> list[int]:
    """Ports LitematicaBitArray.getAt -- entries are packed low-bit-first
    into 64-bit words, an entry may straddle two words, and words are
    signed longs as read from NBT (TAG_Long), so must be masked back to
    unsigned 64-bit before the bit ops below match Java's `>>>` (unsigned
    right shift) semantics.
    """
    mask = (1 << bits_per_entry) - 1
    words = [w & 0xFFFFFFFFFFFFFFFF for w in long_array]
    result = []
    for index in range(count):
        start_offset = index * bits_per_entry
        start_word = start_offset >> 6
        end_word = ((index + 1) * bits_per_entry - 1) >> 6
        start_bit = start_offset & 0x3F
        if start_word == end_word:
            value = (words[start_word] >> start_bit) & mask
        else:
            end_offset = 64 - start_bit
            value = ((words[start_word] >> start_bit) | (words[end_word] << end_offset)) & mask
        result.append(value)
    return result


@dataclass(frozen=True)
class SchematicBlock:
    # Coordinates relative to the schematic's own minimum corner (i.e. the
    # bounding box always starts at (0, 0, 0) here) -- NOT the raw litematic
    # region-local indices, which can run in either direction depending on
    # a negative Size component (see Schematic.from_file's own docstring).
    x: int
    y: int
    z: int
    block: str  # e.g. "minecraft:stone" -- palette Name, Properties dropped (unused so far)


@dataclass(frozen=True)
class Schematic:
    size_x: int
    size_y: int
    size_z: int
    blocks: list[SchematicBlock]  # air excluded -- see from_file's own docstring

    @staticmethod
    def from_file(path: str | Path) -> "Schematic":
        """Reads a `.litematic` file (gzip-compressed NBT) and returns its
        single region's non-air blocks, normalized to a (0,0,0)-based
        bounding box regardless of the file's own Position/Size sign
        conventions.

        Litematica's own Position + (possibly negative-component) Size
        pair describes a box that can extend in ANY direction from
        Position -- e.g. `Size.z = -3` means the region spans z in
        `[Position.z - 2, Position.z]`, not `[Position.z, Position.z + 2]`.
        The block-state container itself only ever stores `abs(Size)` cells
        per axis (confirmed via LitematicaBlockStateContainer's own
        `Math.abs(size.getX/Y/Z)`), indexed `0..size-1` with NO sign
        information -- the sign only matters for where Litematica later
        renders/places that grid relative to Position. Since this reader's
        only caller places the schematic itself (picking its own anchor,
        not trying to reproduce Litematica's exact in-world preview
        position), the sign is dropped entirely here: `blocks` are already
        relative to the schematic's own min corner, ready to add directly
        to any caller-chosen anchor position.

        Only the first region is read -- multi-region schematics (multiple
        selection boxes saved as one file) aren't a scenario this repo's
        tests need yet; raises ValueError if there's more than one, so that
        limitation fails loudly instead of silently dropping regions.
        """
        raw = gzip.decompress(Path(path).read_bytes())
        root = _read_root_compound(raw)

        regions = root["Regions"]
        if len(regions) != 1:
            raise ValueError(f"expected exactly 1 region, found {len(regions)}: {sorted(regions)}")
        region = next(iter(regions.values()))

        size = region["Size"]
        size_x, size_y, size_z = abs(size["x"]), abs(size["y"]), abs(size["z"])

        palette = [entry["Name"] for entry in region["BlockStatePalette"]]
        bits = _bits_per_entry(len(palette))
        total_cells = size_x * size_y * size_z
        indices = _unpack_block_states(region["BlockStates"], bits, total_cells)

        size_layer = size_x * size_z
        blocks = []
        for cy in range(size_y):
            for cz in range(size_z):
                for cx in range(size_x):
                    # Matches LitematicaBlockStateContainer.getIndex exactly:
                    # y*sizeLayer + z*sizeX + x.
                    index = cy * size_layer + cz * size_x + cx
                    name = palette[indices[index]]
                    if name != "minecraft:air":
                        blocks.append(SchematicBlock(x=cx, y=cy, z=cz, block=name))

        return Schematic(size_x=size_x, size_y=size_y, size_z=size_z, blocks=blocks)
