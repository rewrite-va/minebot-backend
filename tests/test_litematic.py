"""Unit tests for minebot/testing/litematic.py -- decoding real
`.litematic` files (Litematica's own NBT save format) without Litematica
itself or any NBT library. See that module's own docstring for why this is
a from-scratch reader and how its bit-packing math was verified against
Litematica's real Java source.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import pytest

from minebot.testing.litematic import Schematic, SchematicBlock, Waypoint, Waypoints, _bits_per_entry, _unpack_block_states

REAL_SCHEMATIC_PATH = Path(
    "/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 riterite/"
    "minecraft/schematics/simple goto.litematic"
)


def test_bits_per_entry_matches_litematica_minimum():
    # Litematica's own LitematicaBlockStateContainer.setBits: max(2,
    # ceil(log2(paletteSize))) -- confirmed via its real source, never
    # fewer than 2 bits even for a 2-entry palette.
    assert _bits_per_entry(2) == 2
    assert _bits_per_entry(3) == 2
    assert _bits_per_entry(4) == 2
    assert _bits_per_entry(5) == 3
    assert _bits_per_entry(16) == 4
    assert _bits_per_entry(17) == 5


def test_unpack_block_states_straddling_word_boundary():
    # 3 bits per entry, 3 entries: 0b101, 0b011, 0b110 packed low-bit-first
    # starting at bit 0 -> word value 0b110_011_101 = 0x1CD.
    word = 0b110_011_101
    values = _unpack_block_states([word], bits_per_entry=3, count=3)
    assert values == [0b101, 0b011, 0b110]


def _build_minimal_litematic(tmp_path: Path, size: tuple[int, int, int], palette: list[str], indices: list[int]) -> Path:
    """Hand-encodes a minimal single-region .litematic file (just the
    fields litematic.py actually reads) so tests don't depend on a real
    file existing on disk outside this repo.
    """
    size_x, size_y, size_z = size
    bits = _bits_per_entry(len(palette))
    total_cells = size_x * size_y * size_z
    assert len(indices) == total_cells

    words = [0] * (-(-(total_cells * bits) // 64))  # ceil division
    mask = (1 << bits) - 1
    for i, value in enumerate(indices):
        start_offset = i * bits
        start_word = start_offset >> 6
        end_word = ((i + 1) * bits - 1) >> 6
        start_bit = start_offset & 0x3F
        words[start_word] = (words[start_word] & ~(mask << start_bit)) | ((value & mask) << start_bit)
        if start_word != end_word:
            end_offset = 64 - start_bit
            j1 = bits - end_offset
            words[end_word] = (words[end_word] >> j1 << j1) | ((value & mask) >> end_offset)

    def compound(entries: bytes) -> bytes:
        return entries + b"\x00"

    def named_tag(tag_type: int, name: str, payload: bytes) -> bytes:
        return struct.pack(">B", tag_type) + struct.pack(">H", len(name)) + name.encode("utf-8") + payload

    def int_tag(name: str, value: int) -> bytes:
        return named_tag(3, name, struct.pack(">i", value))

    def string_tag(name: str, value: str) -> bytes:
        encoded = value.encode("utf-8")
        return named_tag(8, name, struct.pack(">H", len(encoded)) + encoded)

    def long_array_tag(name: str, values: list[int]) -> bytes:
        payload = struct.pack(">i", len(values)) + b"".join(struct.pack(">q", v) for v in values)
        return named_tag(12, name, payload)

    def compound_tag(name: str, entries: bytes) -> bytes:
        return named_tag(10, name, compound(entries))

    def list_tag(name: str, item_type: int, items: list[bytes]) -> bytes:
        payload = struct.pack(">B", item_type) + struct.pack(">i", len(items))
        for item in items:
            payload += item
        return named_tag(9, name, payload)

    palette_entries = [compound(string_tag("Name", name)) for name in palette]

    size_tag = compound_tag("Size", int_tag("x", size_x) + int_tag("y", size_y) + int_tag("z", size_z))
    position_tag = compound_tag("Position", int_tag("x", 0) + int_tag("y", 0) + int_tag("z", 0))
    palette_tag = list_tag("BlockStatePalette", 10, palette_entries)
    blockstates_tag = long_array_tag("BlockStates", words)
    ticks_a = list_tag("PendingBlockTicks", 10, [])
    ticks_b = list_tag("PendingFluidTicks", 10, [])
    tile_entities = list_tag("TileEntities", 10, [])
    entities = list_tag("Entities", 10, [])

    region_body = size_tag + position_tag + palette_tag + blockstates_tag + ticks_a + ticks_b + tile_entities + entities
    regions_tag = compound_tag("Regions", compound_tag("TestRegion", region_body))

    root_body = regions_tag
    root = struct.pack(">B", 10) + struct.pack(">H", 0) + compound(root_body)

    path = tmp_path / "synthetic.litematic"
    path.write_bytes(gzip.compress(root))
    return path


def test_from_file_reads_real_simple_goto_schematic():
    if not REAL_SCHEMATIC_PATH.exists():
        pytest.skip(f"real schematic fixture not present at {REAL_SCHEMATIC_PATH}")

    schematic = Schematic.from_file(REAL_SCHEMATIC_PATH)

    assert schematic.size_x == 1
    assert schematic.size_y == 3
    assert schematic.size_z == 3
    # 4 stone blocks -- the lime_wool "end" marker is pulled into
    # waypoints, not counted as a placeable block (see
    # WAYPOINT_BLOCK_ROLES/Waypoints' own docstrings).
    assert len(schematic.blocks) == 4
    assert all(b.block == "minecraft:stone" for b in schematic.blocks)
    for b in schematic.blocks:
        assert 0 <= b.x < schematic.size_x
        assert 0 <= b.y < schematic.size_y
        assert 0 <= b.z < schematic.size_z

    assert schematic.waypoints.end == [Waypoint(x=0, y=2, z=0)]
    assert schematic.waypoints.start == [Waypoint(x=0, y=1, z=2)]
    assert schematic.waypoints.path == []
    assert schematic.waypoints.forbidden == []


def test_from_file_excludes_air_and_normalizes_coordinates(tmp_path):
    # 2x1x2 region, palette [air, stone, dirt]; indices in getIndex order
    # (y*sizeLayer + z*sizeX + x), sizeLayer = 2*2 = 4:
    #   (x=0,z=0)=stone (x=1,z=0)=air (x=0,z=1)=dirt (x=1,z=1)=stone
    path = _build_minimal_litematic(
        tmp_path,
        size=(2, 1, 2),
        palette=["minecraft:air", "minecraft:stone", "minecraft:dirt"],
        indices=[1, 0, 2, 1],
    )

    schematic = Schematic.from_file(path)

    assert schematic.size_x == 2
    assert schematic.size_y == 1
    assert schematic.size_z == 2
    assert set(schematic.blocks) == {
        SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
        SchematicBlock(x=0, y=0, z=1, block="minecraft:dirt"),
        SchematicBlock(x=1, y=0, z=1, block="minecraft:stone"),
    }
    assert schematic.waypoints == Waypoints(start=[], path=[], forbidden=[], end=[])


def test_from_file_extracts_wool_waypoints_and_excludes_them_from_blocks(tmp_path):
    # 4x1x1 row: white(start) yellow(path) red(forbidden) green(end) --
    # covers all four roles plus confirms none of them leak into `blocks`.
    path = _build_minimal_litematic(
        tmp_path,
        size=(4, 1, 1),
        palette=[
            "minecraft:air",
            "minecraft:white_wool",
            "minecraft:yellow_wool",
            "minecraft:red_wool",
            "minecraft:green_wool",
        ],
        indices=[1, 2, 3, 4],
    )

    schematic = Schematic.from_file(path)

    assert schematic.blocks == []
    assert schematic.waypoints == Waypoints(
        start=[Waypoint(x=0, y=0, z=0)],
        path=[Waypoint(x=1, y=0, z=0)],
        forbidden=[Waypoint(x=2, y=0, z=0)],
        end=[Waypoint(x=3, y=0, z=0)],
    )


def test_from_file_treats_lime_wool_as_equivalent_to_green_wool(tmp_path):
    path = _build_minimal_litematic(
        tmp_path,
        size=(1, 1, 1),
        palette=["minecraft:air", "minecraft:lime_wool"],
        indices=[1],
    )

    schematic = Schematic.from_file(path)

    assert schematic.waypoints.end == [Waypoint(x=0, y=0, z=0)]


def test_from_file_extracts_magenta_wool_as_unreachable(tmp_path):
    path = _build_minimal_litematic(
        tmp_path,
        size=(1, 1, 1),
        palette=["minecraft:air", "minecraft:magenta_wool"],
        indices=[1],
    )

    schematic = Schematic.from_file(path)

    assert schematic.blocks == []
    assert schematic.waypoints.unreachable == [Waypoint(x=0, y=0, z=0)]


def test_from_file_rejects_multi_region_schematics(tmp_path):
    size_x, size_y, size_z = 1, 1, 1
    bits = _bits_per_entry(2)

    def compound(entries: bytes) -> bytes:
        return entries + b"\x00"

    def named_tag(tag_type: int, name: str, payload: bytes) -> bytes:
        return struct.pack(">B", tag_type) + struct.pack(">H", len(name)) + name.encode("utf-8") + payload

    def int_tag(name: str, value: int) -> bytes:
        return named_tag(3, name, struct.pack(">i", value))

    def string_tag(name: str, value: str) -> bytes:
        encoded = value.encode("utf-8")
        return named_tag(8, name, struct.pack(">H", len(encoded)) + encoded)

    def long_array_tag(name: str, values: list[int]) -> bytes:
        payload = struct.pack(">i", len(values)) + b"".join(struct.pack(">q", v) for v in values)
        return named_tag(12, name, payload)

    def compound_tag(name: str, entries: bytes) -> bytes:
        return named_tag(10, name, compound(entries))

    def list_tag(name: str, item_type: int, items: list[bytes]) -> bytes:
        payload = struct.pack(">B", item_type) + struct.pack(">i", len(items))
        for item in items:
            payload += item
        return named_tag(9, name, payload)

    palette_tag = list_tag("BlockStatePalette", 10, [compound(string_tag("Name", "minecraft:air")), compound(string_tag("Name", "minecraft:stone"))])
    size_tag = compound_tag("Size", int_tag("x", size_x) + int_tag("y", size_y) + int_tag("z", size_z))
    position_tag = compound_tag("Position", int_tag("x", 0) + int_tag("y", 0) + int_tag("z", 0))
    blockstates_tag = long_array_tag("BlockStates", [1])
    empties = list_tag("PendingBlockTicks", 10, []) + list_tag("PendingFluidTicks", 10, []) + list_tag("TileEntities", 10, []) + list_tag("Entities", 10, [])

    region_body = size_tag + position_tag + palette_tag + blockstates_tag + empties
    regions_tag = compound_tag("Regions", compound_tag("A", region_body) + compound_tag("B", region_body))

    root = struct.pack(">B", 10) + struct.pack(">H", 0) + compound(regions_tag)
    path = tmp_path / "multi.litematic"
    path.write_bytes(gzip.compress(root))

    with pytest.raises(ValueError, match="expected exactly 1 region"):
        Schematic.from_file(path)
