"""Verifies the heightmap bit-unpacking against SimpleBitStorage's actual
semantics (data.get(index) + minY = firstAvailable, i.e. the first empty Y
above the ground -- see FINDINGS.md/chunks.py docstring), not just an
internally-consistent roundtrip: encodes known height values with the same
packing scheme net.minecraft.util.SimpleBitStorage uses (bits-wide fields,
valuesPerLong = 64 // bits per long, LSB-first within each long) and checks
we recover them and compute the right ground Y.
"""

from __future__ import annotations

from minebot.net.types import ByteWriter
from minebot.protocol.chunks import (
    HEIGHTMAP_TYPE_MOTION_BLOCKING,
    HEIGHTMAP_TYPE_WORLD_SURFACE,
    ChunkHeightmapCache,
    _infer_bits_from_long_count,
    _unpack_heightmap,
    parse_chunk_heightmaps,
    parse_level_chunk_with_light,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def _pack_bitstorage(values: list[int], bits: int) -> list[int]:
    """Encodes `values` (256 entries) the same way SimpleBitStorage does:
    valuesPerLong = 64 // bits, entry `index` at
    data[index // valuesPerLong], bit offset (index % valuesPerLong) * bits.
    """
    values_per_long = 64 // bits
    num_longs = -(-len(values) // values_per_long)
    longs = [0] * num_longs
    mask = (1 << bits) - 1
    for index, value in enumerate(values):
        cell_index = index // values_per_long
        bit_offset = (index % values_per_long) * bits
        longs[cell_index] |= (value & mask) << bit_offset
    # Convert to signed 64-bit (Java long semantics) for consistency with
    # how the wire format stores/reads them.
    return [v - (1 << 64) if v >= (1 << 63) else v for v in longs]


def test_infer_bits_from_long_count_matches_known_overworld_case():
    # height=384 (-64..320, the standard modern overworld) -> bits=9,
    # valuesPerLong=7, ceil(256/7)=37 longs.
    assert _infer_bits_from_long_count(37) == 9


def test_infer_bits_raises_when_no_match():
    import pytest

    from minebot.protocol.chunks import ChunkParseError

    with pytest.raises(ChunkParseError):
        _infer_bits_from_long_count(999999)


def test_unpack_heightmap_roundtrip():
    bits = 9
    values = [(i * 7) % (1 << bits) for i in range(256)]
    longs = _pack_bitstorage(values, bits)

    unpacked = _unpack_heightmap(longs, bits)
    assert unpacked == values


def _encode_heightmaps_packet(chunk_x: int, chunk_z: int, heightmaps: dict[int, tuple[list[int], int]]) -> bytes:
    """heightmaps: {type: (values, bits)}"""
    writer = ByteWriter()
    writer.write_i32(chunk_x)
    writer.write_i32(chunk_z)
    writer.write_varint(len(heightmaps))
    for heightmap_type, (values, bits) in heightmaps.items():
        writer.write_varint(heightmap_type)
        longs = _pack_bitstorage(values, bits)
        writer.write_varint(len(longs))
        for long_value in longs:
            writer.write_i64(long_value)
    return writer.getvalue()


def test_parse_chunk_heightmaps_basic():
    bits = 9
    motion_blocking_values = [100] * 256
    motion_blocking_values[0] = 65  # first column has a lower ground height

    data = _encode_heightmaps_packet(
        3, -2, {HEIGHTMAP_TYPE_MOTION_BLOCKING: (motion_blocking_values, bits)}
    )

    chunk = parse_chunk_heightmaps(data)
    assert chunk.chunk_x == 3
    assert chunk.chunk_z == -2
    assert chunk.raw_height_at(0, 0, HEIGHTMAP_TYPE_MOTION_BLOCKING) == 65
    assert chunk.raw_height_at(1, 0, HEIGHTMAP_TYPE_MOTION_BLOCKING) == 100
    assert chunk.raw_height_at(0, 0, HEIGHTMAP_TYPE_WORLD_SURFACE) is None


def test_parse_level_chunk_with_light_dispatches_by_packet_id():
    bits = 9
    values = [64] * 256
    data = _encode_heightmaps_packet(0, 0, {HEIGHTMAP_TYPE_MOTION_BLOCKING: (values, bits)})

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LEVEL_CHUNK_WITH_LIGHT")
    result = parse_level_chunk_with_light(packet_id, data)
    assert result is not None
    assert result.chunk_x == 0


def test_parse_level_chunk_with_light_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_level_chunk_with_light(packet_id, b"\x00" * 8) is None


def test_chunk_heightmap_cache_ground_height_at_world_coordinates():
    bits = 9
    # Raw value 65 means firstAvailable = min_y + 65; with min_y=-64 that's
    # Y=1, so the highest solid block (getHighestTaken) is Y=0.
    values = [65] * 256
    data = _encode_heightmaps_packet(0, 0, {HEIGHTMAP_TYPE_MOTION_BLOCKING: (values, bits)})
    chunk = parse_chunk_heightmaps(data)

    cache = ChunkHeightmapCache(min_y=-64)
    cache.handle_chunk(chunk)

    # world (5.5, 10.2) -> block (5, 10) -> chunk (0, 0), local (5, 10)
    assert cache.ground_height_at(5.5, 10.2) == 0


def test_chunk_heightmap_cache_returns_none_for_unknown_chunk():
    cache = ChunkHeightmapCache()
    assert cache.ground_height_at(1000.0, 1000.0) is None


def test_chunk_heightmap_cache_handles_negative_world_coordinates():
    bits = 9
    values = [65] * 256
    data = _encode_heightmaps_packet(-1, -1, {HEIGHTMAP_TYPE_MOTION_BLOCKING: (values, bits)})
    chunk = parse_chunk_heightmaps(data)

    cache = ChunkHeightmapCache(min_y=-64)
    cache.handle_chunk(chunk)

    # world (-1.5, -1.5) -> block (-2, -2) -> chunk (-1, -1), local (14, 14)
    assert cache.ground_height_at(-1.5, -1.5) == 0
