"""Real per-block parsing of ClientboundLevelChunkWithLightPacket's paletted
block-state container -- the prerequisite for A* pathfinding identified in
FINDINGS.md ("Why this needs real pathfinding"): heightmaps (chunks.py) only
answer "what's the tallest block in this column," which can't tell solid
ground from a roof/ceiling, or see gaps/overhangs at all. This module
decodes the actual block-state id present at every (x, y, z), letting
solidity be looked up per-block via block_registry.py.

Field layout (from the decompiled source, PalettedContainer.read /
LevelChunkSection.read / net.minecraft.world.level.chunk.Strategy):

  Each ClientboundLevelChunkPacketData carries one LevelChunkSection per
  16x16x16 vertical section stacked bottom-to-top, read back-to-back with
  no explicit count -- the packet's own varint-prefixed byte-buffer length
  is the only framing, so sections are read until that buffer is exhausted
  (mirrors what LevelChunkSection.write/read actually do: no section count
  is ever written, since the reader already knows the dimension's height --
  which we don't parse; see min_y/section-count note below).

  LevelChunkSection.read: nonEmptyBlockCount:i16, fluidCount:i16 (both
  unused here -- only meaningful for server-side tick optimization), then
  a PalettedContainer<BlockState> (block states), then a
  PalettedContainer<Holder<Biome>> (biomes, skipped -- see
  _skip_paletted_container).

  PalettedContainer.read: bits:u8 (the *wire-declared* bit count -- not
  necessarily the actual in-memory storage width; see below), then a
  palette, then a bare fixed-size packed long array:
    wire bits == 0        -> SingleValuePalette: one palette entry, a
                             varint global block-state id, applied to
                             every entry; no packed long array at all
                             (ZeroBitStorage).
    wire bits small       -> Linear or HashMap palette: varint palette-
                             entry count, then that many varint global ids
                             (the local palette), then the packed index
                             array. Blocks (Strategy.createForBlockStates)
                             pad wire bits 1-4 to a fixed *storage* width of
                             4 (Strategy.FOUR_BITS_LINEAR), and store wire
                             bits 5-8 at their own declared width (5/6/7/8,
                             the HashMap range); biomes
                             (Strategy.createForBiomes) have no padding and
                             no HashMap range at all -- wire bits 1-3 are
                             each stored at their own exact width, with
                             nothing between 3 and Global. See
                             _block_storage_bits/_biome_storage_bits.
    wire bits above that   -> GlobalPalette: no local palette at all (its
                             read() is a no-op) -- the packed index array's
                             values themselves ARE the real global ids
                             directly, stored at exactly the wire's own bit
                             count.
    (all cases): the packed long array is a **bare, unprefixed** long[]
    (unlike heightmaps' varint-prefixed LONG_ARRAY): a fixed count of raw
    big-endian i64s, count = ceil(entry_count / (64 // storage_bits))
    (SimpleBitStorage's own sizing, same bit-packing scheme already used by
    chunks.py's heightmap parsing -- no cross-long value splitting).

Biomes (also paletted, right after each section's block states) are parsed
structurally to correctly skip past them, but never interpreted -- we have
no biome registry and no current use for biome data. Block-entity NBT
(after all sections) is also not parsed; irrelevant to pathfinding and,
being a full ClientboundLevelChunkPacketData tail, safe to simply never
reach since we only need the sections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from minebot.net.types import ByteReader
from minebot.protocol.block_registry import BLOCK_REGISTRY
from minebot.protocol.registry import REGISTRY

STATE = "play"

_BLOCK_ENTRIES_PER_SECTION = 4096  # 16 * 16 * 16
_BIOME_ENTRIES_PER_SECTION = 64  # 4 * 4 * 4
SECTION_SIZE = 16


def _read_packed_long_array(reader: ByteReader, entry_count: int, bits: int) -> list[int]:
    if bits == 0:
        return []
    values_per_long = 64 // bits
    long_count = -(-entry_count // values_per_long)  # ceil
    raw_longs = [reader.read_i64() for _ in range(long_count)]

    mask = (1 << bits) - 1
    values = []
    for index in range(entry_count):
        cell_index = index // values_per_long
        bit_offset = (index % values_per_long) * bits
        values.append((raw_longs[cell_index] >> bit_offset) & mask)
    return values


def _read_paletted_container(reader: ByteReader, entry_count: int, storage_bits_for_wire_bits) -> list[int]:
    """Generic paletted-container reader shared by block states (4096
    entries/section, Strategy.createForBlockStates) and biomes (64
    entries/section, Strategy.createForBiomes) -- both use the exact same
    wire *shape*, but the two strategies map a given wire-declared bit
    count to a different actual in-memory storage bit width for their
    "small" (non-Global) palettes (see `storage_bits_for_wire_bits`), which
    matters even when we only care about correctly skipping past the bytes
    (biomes) rather than the resolved values -- get the bit width wrong and
    every subsequent byte in the chunk misaligns.

    Returns `entry_count` raw values: for block states these are already-
    resolved global block-state ids; for biomes (only ever skipped, never
    interpreted) the caller discards them.
    """
    wire_bits = reader.read_u8()

    if wire_bits == 0:
        # SingleValuePalette: exactly one palette entry, applied to every
        # entry; ZeroBitStorage backs it (no packed long array at all --
        # Strategy.ZERO_BITS has bitsInMemory()==0).
        single_value = reader.read_varint()
        return [single_value] * entry_count

    storage_bits = storage_bits_for_wire_bits(wire_bits)
    if storage_bits is not None:
        # Linear or HashMap: both carry an explicit local palette (varint
        # count + that many varint global ids) ahead of the packed index
        # array -- structurally identical, they only differ in what bit
        # width backs the index array (see storage_bits_for_wire_bits).
        palette_count = reader.read_varint()
        palette_entries = [reader.read_varint() for _ in range(palette_count)]
        indices = _read_packed_long_array(reader, entry_count, storage_bits)
        return [palette_entries[index] for index in indices]

    # GlobalPalette: no local palette (its read() is a no-op -- see the
    # decompiled GlobalPalette.java), so the packed values themselves are
    # already the real global ids, stored at exactly the wire's own bit
    # count.
    return _read_packed_long_array(reader, entry_count, wire_bits)


def _block_storage_bits(wire_bits: int) -> int | None:
    """Strategy.createForBlockStates: wire bits 1-4 all padded to a fixed 4
    (Strategy.FOUR_BITS_LINEAR), 5-8 stored at their own declared bit count
    (Strategy.{FIVE,SIX,SEVEN,EIGHT}_BITS_HASHMAP), above 8 is Global (None
    here, handled by the caller falling through to the packed-long-array
    path directly at the wire's own bit count).
    """
    if wire_bits <= 4:
        return 4
    if wire_bits <= 8:
        return wire_bits
    return None


def _biome_storage_bits(wire_bits: int) -> int | None:
    """Strategy.createForBiomes: wire bits 1-3 each stored at their own
    exact declared bit count (Strategy.{ONE,TWO,THREE}_BIT(S)_LINEAR -- no
    padding, and no HashMap range at all for biomes), above 3 is Global.
    """
    if wire_bits <= 3:
        return wire_bits
    return None


def _read_paletted_block_states(reader: ByteReader) -> list[int]:
    """Returns 4096 global block-state ids, index = (y << 8 | z << 4 | x)
    (Strategy.createForBlockStates uses bitsPerAxis=4, getIndex computes
    `(y << 4 | z) << 4 | x`, confirmed against the decompiled Strategy.java).
    """
    return _read_paletted_container(reader, _BLOCK_ENTRIES_PER_SECTION, _block_storage_bits)


def _skip_paletted_biomes(reader: ByteReader) -> None:
    """Structurally parses (to correctly advance past) the biome container
    that follows every section's block states -- we have no biome registry
    and no current use for biome data, but must still walk past it
    correctly to reach the next section.
    """
    _read_paletted_container(reader, _BIOME_ENTRIES_PER_SECTION, _biome_storage_bits)


def _read_section(reader: ByteReader) -> list[int]:
    """LevelChunkSection.read: nonEmptyBlockCount:i16, fluidCount:i16 (both
    unused here -- purely server-side tick-optimization counters), then the
    block-state paletted container, then the biome paletted container
    (parsed only to skip past it correctly).
    """
    reader.read(2)  # nonEmptyBlockCount
    reader.read(2)  # fluidCount
    block_states = _read_paletted_block_states(reader)
    _skip_paletted_biomes(reader)
    return block_states


@dataclass
class ChunkSectionBlocks:
    """One 16x16x16 section's block-state ids, index = (y << 8 | z << 4 | x)
    (local x/y/z all 0-15 within the section).
    """

    section_y: int  # section index; world Y of this section's y=0 is section_y * SECTION_SIZE
    block_state_ids: list[int]

    def state_at(self, local_x: int, local_y: int, local_z: int) -> int:
        return self.block_state_ids[(local_y << 8) | (local_z << 4) | local_x]


@dataclass
class ChunkBlocks:
    chunk_x: int
    chunk_z: int
    sections: list[ChunkSectionBlocks]  # bottom-to-top, as read off the wire

    def state_at(self, world_x: int, world_y: int, world_z: int, min_y: int) -> int | None:
        """Returns the global block-state id at this world position, or
        None if it falls outside the sections we actually received (e.g.
        below min_y or above the dimension's height -- we don't know the
        dimension's real height since it's unparsed registry data, so this
        is simply "outside every section this chunk packet carried").
        """
        section_index = (world_y - min_y) // SECTION_SIZE
        if section_index < 0 or section_index >= len(self.sections):
            return None
        section = self.sections[section_index]
        local_x = world_x & 15
        local_y = (world_y - min_y) % SECTION_SIZE
        local_z = world_z & 15
        return section.state_at(local_x, local_y, local_z)


def parse_chunk_blocks(chunk_x: int, chunk_z: int, block_data: bytes) -> ChunkBlocks:
    """Parses the raw per-section block buffer inside
    ClientboundLevelChunkPacketData -- i.e. the varint-length-prefixed byte
    buffer that chunks.py's heightmap parsing deliberately stops short of.
    Reads sections back-to-back (no explicit count on the wire -- see
    module docstring) until `block_data` is exhausted.
    """
    reader = ByteReader(block_data)
    sections = []
    section_y = 0
    while reader.remaining() > 0:
        block_state_ids = _read_section(reader)
        sections.append(ChunkSectionBlocks(section_y=section_y, block_state_ids=block_state_ids))
        section_y += 1
    return ChunkBlocks(chunk_x=chunk_x, chunk_z=chunk_z, sections=sections)


def parse_level_chunk_block_data(packet_id: int, data: bytes) -> ChunkBlocks | None:
    """Parses a ClientboundLevelChunkWithLightPacket for its block data (the
    part chunks.py's heightmap parsing deliberately never reaches -- that
    module stops right after the heightmaps, since a varint-prefixed byte
    buffer follows them holding exactly what this function decodes).
    """
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_LEVEL_CHUNK_WITH_LIGHT":
        return None

    reader = ByteReader(data)
    chunk_x = reader.read_i32()
    chunk_z = reader.read_i32()

    heightmap_count = reader.read_varint()
    for _ in range(heightmap_count):
        reader.read_varint()  # heightmap type
        long_count = reader.read_varint()
        reader.read(long_count * 8)  # skip the raw heightmap longs

    block_data = reader.read_byte_array()
    return parse_chunk_blocks(chunk_x, chunk_z, block_data)


DEFAULT_OVERWORLD_MIN_Y = -64


@dataclass
class ChunkBlockCache:
    """chunk (x, z) -> ChunkBlocks, plus a world-coordinate block-state
    query. Companion to chunks.py's ChunkHeightmapCache: that answers "how
    tall is this column," this answers "what's actually at this exact
    (x, y, z)" -- the missing piece for real solid/air-aware pathfinding
    (see FINDINGS.md "Why this needs real pathfinding").
    """

    min_y: int = DEFAULT_OVERWORLD_MIN_Y
    _chunks: dict[tuple[int, int], ChunkBlocks] = field(default_factory=dict)

    def handle_chunk(self, chunk: ChunkBlocks) -> None:
        self._chunks[(chunk.chunk_x, chunk.chunk_z)] = chunk

    def block_state_at(self, world_x: float, world_z: float, world_y: float) -> int | None:
        block_x = math.floor(world_x)
        block_y = math.floor(world_y)
        block_z = math.floor(world_z)
        chunk_x, chunk_z = block_x >> 4, block_z >> 4
        chunk = self._chunks.get((chunk_x, chunk_z))
        if chunk is None:
            return None
        return chunk.state_at(block_x, block_y, block_z, self.min_y)

    def is_solid(self, world_x: float, world_z: float, world_y: float) -> bool | None:
        """True/False if we have data for this position, None if the
        containing chunk hasn't been received yet -- callers should treat
        None as "unknown," not as either solid or air.
        """
        state_id = self.block_state_at(world_x, world_z, world_y)
        if state_id is None:
            return None
        return BLOCK_REGISTRY.is_solid(state_id)
