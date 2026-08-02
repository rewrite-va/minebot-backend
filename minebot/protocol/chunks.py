"""Ground-height tracking via ClientboundLevelChunkWithLightPacket's
heightmaps -- the cheap first step toward real movement (see FINDINGS.md
"Why this needs real pathfinding"): heightmaps are precomputed server-side
per (x,z) column and answer "what's the ground height here" without
needing full per-block paletted-container parsing at all.

Field layout (from the decompiled source):
  ClientboundLevelChunkWithLightPacket: x, z: i32, then
      ClientboundLevelChunkPacketData, then light data (not needed here,
      and never parsed -- we stop reading once we have the heightmaps).
  ClientboundLevelChunkPacketData: heightmaps: Map<Heightmap.Types, long[]>,
      then a varint-prefixed raw byte buffer (the actual per-section
      paletted block data -- NOT parsed by this module), then block
      entities (also not parsed).
  Heightmap.Types is a VarInt of its `id` field (0-5, only two of which
      -- WORLD_SURFACE=1 and MOTION_BLOCKING=4 -- have sendToClient()=true
      and therefore actually appear on the wire).
  Map<Heightmap.Types, long[]>: VarInt entry count, then that many
      (type: varint, long[]) pairs.
  long[] (ByteBufCodecs.LONG_ARRAY): VarInt count, then that many raw
      big-endian i64s.

Bit-packing (net.minecraft.util.SimpleBitStorage, read directly off the
decompiled source rather than assumed): 256 entries (16x16 columns) packed
`bits`-wide, `valuesPerLong = 64 // bits` values per long, entry `index`
lives at `data[index // valuesPerLong]`, bit offset
`(index % valuesPerLong) * bits`. `bits = ceil(log2(dimension_height + 1))`,
where dimension_height is itself part of the (currently unparsed)
dimension-type registry data -- rather than hardcode a specific dimension's
height (fragile: breaks for the Nether/End/custom worlds, and this session
targets a real server we don't control), `bits` is instead reverse-derived
from the observed long-array length, which is unambiguous for any
realistic Minecraft world height (see FINDINGS.md for the derivation and
its limits).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from minebot.net.types import ByteReader
from minebot.protocol.registry import REGISTRY

STATE = "play"

HEIGHTMAP_TYPE_WORLD_SURFACE = 1
HEIGHTMAP_TYPE_MOTION_BLOCKING = 4

_ENTRIES_PER_HEIGHTMAP = 256  # 16x16 columns


class ChunkParseError(Exception):
    pass


def _infer_bits_from_long_count(num_longs: int) -> int:
    """Reverses `num_longs = ceil(256 / (64 // bits))` to recover `bits`,
    without needing the dimension height that produced it. Unambiguous for
    realistic Minecraft world heights (bits 1..10, i.e. dimension heights up
    to 1023 blocks -- vastly more than any vanilla or sane custom
    dimension); raises above that rather than silently guessing wrong.
    """
    for bits in range(1, 11):
        values_per_long = 64 // bits
        expected_longs = -(-_ENTRIES_PER_HEIGHTMAP // values_per_long)
        if expected_longs == num_longs:
            return bits
    raise ChunkParseError(
        f"could not infer heightmap bit width from a {num_longs}-long array "
        "(dimension height would need to exceed ~1023 blocks)"
    )


def _unpack_heightmap(raw_longs: list[int], bits: int) -> list[int]:
    """Returns 256 raw height values (0..2^bits-1, still relative to the
    dimension's min_y -- see decode_ground_heights for the min_y offset).
    """
    values_per_long = 64 // bits
    mask = (1 << bits) - 1
    values = []
    for index in range(_ENTRIES_PER_HEIGHTMAP):
        cell_index = index // values_per_long
        bit_offset = (index % values_per_long) * bits
        cell_value = raw_longs[cell_index]
        values.append((cell_value >> bit_offset) & mask)
    return values


@dataclass
class ChunkHeightmap:
    chunk_x: int
    chunk_z: int
    # Raw heightmap values, NOT yet offset by the dimension's min_y (which
    # we don't currently know -- see module docstring). Callers wanting an
    # absolute world-Y ground height must add their own known min_y.
    heights_by_type: dict[int, list[int]]

    def raw_height_at(self, local_x: int, local_z: int, heightmap_type: int = HEIGHTMAP_TYPE_MOTION_BLOCKING) -> int | None:
        """local_x/local_z are 0..15 within this chunk. Returns the number
        of blocks from the dimension's min_y up to (and including) the
        highest column entry matching `heightmap_type`, or None if that
        heightmap type wasn't present in this packet.
        """
        heights = self.heights_by_type.get(heightmap_type)
        if heights is None:
            return None
        return heights[local_z * 16 + local_x]


def parse_chunk_heightmaps(data: bytes) -> ChunkHeightmap:
    reader = ByteReader(data)
    chunk_x = reader.read_i32()
    chunk_z = reader.read_i32()

    heightmap_count = reader.read_varint()
    heights_by_type: dict[int, list[int]] = {}

    for _ in range(heightmap_count):
        heightmap_type = reader.read_varint()
        long_count = reader.read_varint()
        raw_longs = [reader.read_i64() for _ in range(long_count)]

        bits = _infer_bits_from_long_count(long_count)
        heights_by_type[heightmap_type] = _unpack_heightmap(raw_longs, bits)

    return ChunkHeightmap(chunk_x=chunk_x, chunk_z=chunk_z, heights_by_type=heights_by_type)


def parse_level_chunk_with_light(packet_id: int, data: bytes) -> ChunkHeightmap | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_LEVEL_CHUNK_WITH_LIGHT":
        return None
    return parse_chunk_heightmaps(data)


# Standard modern Java Edition overworld min_y (since the 1.18 world-height
# expansion: -64..320). Dimension min_y is actually part of the
# dimension-type registry data (CLIENTBOUND_REGISTRY_DATA), which we don't
# parse -- this is a reasonable default for connecting to a normal
# overworld, but will be wrong for the Nether (min_y=0), a custom
# dimension, or an old-format world. Good enough for the ground-height
# use case (following a player who's also presumably in the overworld);
# revisit if/when dimension-type registry parsing gets built.
DEFAULT_OVERWORLD_MIN_Y = -64


@dataclass
class ChunkHeightmapCache:
    """chunk (x, z) -> ChunkHeightmap, plus a world-coordinate ground-height
    query. Fed by the PLAY loop as ClientboundLevelChunkWithLightPacket
    packets arrive.
    """

    min_y: int = DEFAULT_OVERWORLD_MIN_Y
    _chunks: dict[tuple[int, int], ChunkHeightmap] = field(default_factory=dict)

    def handle_chunk(self, heightmap: ChunkHeightmap) -> None:
        self._chunks[(heightmap.chunk_x, heightmap.chunk_z)] = heightmap

    def ground_height_at(
        self, world_x: float, world_z: float, heightmap_type: int = HEIGHTMAP_TYPE_MOTION_BLOCKING
    ) -> float | None:
        """Returns the world-space Y of the highest solid-ish block at this
        (x, z) column (per `heightmap_type`; MOTION_BLOCKING treats fluids
        as solid too, matching where a player would actually stand/float),
        or None if we haven't received that chunk yet.
        """
        block_x = math.floor(world_x)
        block_z = math.floor(world_z)
        chunk_x, chunk_z = block_x >> 4, block_z >> 4
        chunk = self._chunks.get((chunk_x, chunk_z))
        if chunk is None:
            return None

        local_x = block_x & 15
        local_z = block_z & 15
        raw_height = chunk.raw_height_at(local_x, local_z, heightmap_type)
        if raw_height is None:
            return None

        # The heightmap stores "number of blocks above min_y that are
        # non-air up to and including the highest match", i.e. the height
        # value itself already equals the world Y of the first empty space
        # above the ground -- one block above the actual walkable surface.
        return self.min_y + raw_height - 1
