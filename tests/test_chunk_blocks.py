"""Verifies real per-section block-state parsing against hand-encoded
packets matching net.minecraft.world.level.chunk.PalettedContainer's actual
wire format for each palette kind (SingleValue/Linear/HashMap/Global), not
just an internally-consistent roundtrip -- mirrors the approach in
test_chunks.py for heightmaps.
"""

from __future__ import annotations

from minebot.net.types import ByteWriter
from minebot.protocol.chunk_blocks import (
    SECTION_SIZE,
    ChunkBlockCache,
    parse_chunk_blocks,
    parse_level_chunk_block_data,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def _pack_bits(values: list[int], bits: int) -> list[int]:
    """Same LSB-first, non-splitting SimpleBitStorage packing already used
    by test_chunks.py, generalized to any entry count.
    """
    if bits == 0:
        return []
    values_per_long = 64 // bits
    num_longs = -(-len(values) // values_per_long)
    longs = [0] * num_longs
    mask = (1 << bits) - 1
    for index, value in enumerate(values):
        cell_index = index // values_per_long
        bit_offset = (index % values_per_long) * bits
        longs[cell_index] |= (value & mask) << bit_offset
    return [v - (1 << 64) if v >= (1 << 63) else v for v in longs]


def _write_single_value_container(writer: ByteWriter, value: int) -> None:
    writer.write_u8(0)
    writer.write_varint(value)


def _write_linear_or_hashmap_container(
    writer: ByteWriter, entry_count: int, wire_bits: int, storage_bits: int, palette: list[int], indices: list[int]
) -> None:
    writer.write_u8(wire_bits)
    writer.write_varint(len(palette))
    for entry in palette:
        writer.write_varint(entry)
    for long_value in _pack_bits(indices, storage_bits):
        writer.write_i64(long_value)


def _write_global_container(writer: ByteWriter, wire_bits: int, global_ids: list[int]) -> None:
    writer.write_u8(wire_bits)
    for long_value in _pack_bits(global_ids, wire_bits):
        writer.write_i64(long_value)


def _write_section(
    writer: ByteWriter,
    block_container_writer,
    biome_wire_bits: int = 0,
    biome_value: int = 0,
) -> None:
    writer.write_u16(0)  # nonEmptyBlockCount
    writer.write_u16(0)  # fluidCount
    block_container_writer(writer)
    # Biome container: SingleValuePalette by default (bits=0), matching a
    # section with no interesting biome data -- we never interpret this,
    # just need it structurally present so the reader lands on the next
    # section correctly.
    if biome_wire_bits == 0:
        _write_single_value_container(writer, biome_value)
    else:
        raise NotImplementedError("only bits=0 biome fixture needed for these tests")


def test_single_value_palette_section_all_one_block():
    writer = ByteWriter()
    _write_section(writer, lambda w: _write_single_value_container(w, 42))
    chunk = parse_chunk_blocks(0, 0, writer.getvalue())

    assert len(chunk.sections) == 1
    section = chunk.sections[0]
    for local_y in range(16):
        for local_z in range(16):
            for local_x in range(16):
                assert section.state_at(local_x, local_y, local_z) == 42


def test_linear_palette_section_padded_to_four_bits():
    # wire_bits=2 (Strategy.createForBlockStates maps this to storage_bits=4
    # -- FOUR_BITS_LINEAR covers wire bits 1-4 uniformly), 3 palette entries.
    palette = [0, 5, 100]
    indices = [(i % 3) for i in range(4096)]

    writer = ByteWriter()

    def write_blocks(w: ByteWriter) -> None:
        _write_linear_or_hashmap_container(w, 4096, wire_bits=2, storage_bits=4, palette=palette, indices=indices)

    _write_section(writer, write_blocks)
    chunk = parse_chunk_blocks(0, 0, writer.getvalue())

    section = chunk.sections[0]
    for i in range(4096):
        local_x = i & 15
        local_z = (i >> 4) & 15
        local_y = i >> 8
        expected = palette[i % 3]
        assert section.state_at(local_x, local_y, local_z) == expected


def test_hashmap_palette_section_stores_at_wire_bit_count():
    # wire_bits=6 is within the HashMap range (5-8): stored at exactly 6
    # bits, unlike the Linear range's fixed padding to 4.
    palette = list(range(50))  # needs > 4 bits to address (2^4=16 < 50)
    indices = [(i * 7) % 50 for i in range(4096)]

    writer = ByteWriter()

    def write_blocks(w: ByteWriter) -> None:
        _write_linear_or_hashmap_container(w, 4096, wire_bits=6, storage_bits=6, palette=palette, indices=indices)

    _write_section(writer, write_blocks)
    chunk = parse_chunk_blocks(0, 0, writer.getvalue())

    section = chunk.sections[0]
    for i in [0, 1, 4095, 2048]:
        local_x = i & 15
        local_z = (i >> 4) & 15
        local_y = i >> 8
        expected = palette[indices[i]]
        assert section.state_at(local_x, local_y, local_z) == expected


def test_global_palette_section_values_are_ids_directly():
    # wire_bits=12 (above the HashMap ceiling of 8) -> GlobalPalette: no
    # local palette, packed values are real global block-state ids already.
    global_ids = [(i * 13) % 4000 for i in range(4096)]

    writer = ByteWriter()

    def write_blocks(w: ByteWriter) -> None:
        _write_global_container(w, wire_bits=12, global_ids=global_ids)

    _write_section(writer, write_blocks)
    chunk = parse_chunk_blocks(0, 0, writer.getvalue())

    section = chunk.sections[0]
    for i in [0, 1, 4095, 2048]:
        local_x = i & 15
        local_z = (i >> 4) & 15
        local_y = i >> 8
        assert section.state_at(local_x, local_y, local_z) == global_ids[i]


def test_multiple_sections_stack_bottom_to_top():
    writer = ByteWriter()
    _write_section(writer, lambda w: _write_single_value_container(w, 1))
    _write_section(writer, lambda w: _write_single_value_container(w, 2))
    _write_section(writer, lambda w: _write_single_value_container(w, 3))

    chunk = parse_chunk_blocks(0, 0, writer.getvalue())
    assert len(chunk.sections) == 3
    assert chunk.sections[0].state_at(0, 0, 0) == 1
    assert chunk.sections[1].state_at(0, 0, 0) == 2
    assert chunk.sections[2].state_at(0, 0, 0) == 3


def test_chunk_state_at_maps_world_y_to_correct_section():
    writer = ByteWriter()
    _write_section(writer, lambda w: _write_single_value_container(w, 10))  # section_y=0 -> world Y -64..-49
    _write_section(writer, lambda w: _write_single_value_container(w, 20))  # section_y=1 -> world Y -48..-33

    chunk = parse_chunk_blocks(0, 0, writer.getvalue())
    min_y = -64

    assert chunk.state_at(0, -64, 0, min_y) == 10
    assert chunk.state_at(0, -49, 0, min_y) == 10
    assert chunk.state_at(0, -48, 0, min_y) == 20
    assert chunk.state_at(0, -65, 0, min_y) is None  # below every section
    assert chunk.state_at(0, -32, 0, min_y) is None  # above every section we have


def test_parse_level_chunk_block_data_full_packet():
    block_writer = ByteWriter()
    _write_section(block_writer, lambda w: _write_single_value_container(w, 77))
    block_bytes = block_writer.getvalue()

    full_writer = ByteWriter()
    full_writer.write_i32(2)  # chunk_x
    full_writer.write_i32(-3)  # chunk_z
    full_writer.write_varint(0)  # heightmap_count -- none needed for this test
    full_writer.write_varint(len(block_bytes))
    full_writer.write(block_bytes)

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_LEVEL_CHUNK_WITH_LIGHT")
    chunk = parse_level_chunk_block_data(packet_id, full_writer.getvalue())

    assert chunk is not None
    assert chunk.chunk_x == 2
    assert chunk.chunk_z == -3
    assert chunk.sections[0].state_at(0, 0, 0) == 77


def test_parse_level_chunk_block_data_returns_none_for_other_packets():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_level_chunk_block_data(packet_id, b"\x00" * 8) is None


def test_chunk_block_cache_is_solid_uses_real_registry():
    stone_id = 1  # minecraft:stone, confirmed solid via block_registry_775.json
    writer = ByteWriter()
    _write_section(writer, lambda w: _write_single_value_container(w, stone_id))
    chunk = parse_chunk_blocks(0, 0, writer.getvalue())

    cache = ChunkBlockCache(min_y=-64)
    cache.handle_chunk(chunk)

    assert cache.is_solid(5.5, 10.2, -64.0) is True


def test_chunk_block_cache_returns_none_for_unknown_chunk():
    cache = ChunkBlockCache()
    assert cache.is_solid(1000.0, 1000.0, 0.0) is None
    assert cache.block_state_at(1000.0, 1000.0, 0.0) is None
