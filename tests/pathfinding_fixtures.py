"""Shared synthetic-world builder for pathfinding tests -- constructs a
ChunkBlockCache directly from a simple {(x, z): ground_y} map rather than
encoding real chunk packets, since these tests only need control over which
(x, y, z) positions are solid, not wire-format correctness (that's already
covered by tests/test_chunk_blocks.py).
"""

from __future__ import annotations

from minebot.protocol.chunk_blocks import ChunkBlocks, ChunkSectionBlocks, SECTION_SIZE, ChunkBlockCache

AIR = 0
STONE = 1
MIN_Y = -64
_SECTIONS_PER_CHUNK = 8  # covers world Y -64..63, plenty for these tests


def build_world(ground_at: dict[tuple[int, int], int], extra_solid: set[tuple[int, int, int]] = frozenset()) -> ChunkBlockCache:
    """ground_at: {(x, z): ground_y} -- ground_y is the single solid block's
    Y in that column (everything else in the column is air). Columns not
    present in ground_at are pure air within their chunk (so still
    "known", just no floor -- a bottomless-within-the-fixture pit).
    extra_solid: additional individual (x, y, z) positions to mark solid,
    e.g. for building a wall next to an otherwise-flat floor.
    """
    cache = ChunkBlockCache(min_y=MIN_Y)
    chunk_columns: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
    for (x, z), ground_y in ground_at.items():
        chunk_x, chunk_z = x >> 4, z >> 4
        chunk_columns.setdefault((chunk_x, chunk_z), {})[(x & 15, z & 15)] = ground_y

    extra_by_chunk: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for x, y, z in extra_solid:
        chunk_x, chunk_z = x >> 4, z >> 4
        extra_by_chunk.setdefault((chunk_x, chunk_z), []).append((x & 15, y, z & 15))
        chunk_columns.setdefault((chunk_x, chunk_z), {})

    all_chunks = set(chunk_columns.keys()) | set(extra_by_chunk.keys())
    for chunk_x, chunk_z in all_chunks:
        local_ground = chunk_columns.get((chunk_x, chunk_z), {})
        local_extra = extra_by_chunk.get((chunk_x, chunk_z), [])

        sections = []
        for section_y in range(_SECTIONS_PER_CHUNK):
            base_world_y = MIN_Y + section_y * SECTION_SIZE
            ids = [AIR] * 4096
            for (local_x, local_z), ground_y in local_ground.items():
                if base_world_y <= ground_y < base_world_y + SECTION_SIZE:
                    local_y = ground_y - base_world_y
                    ids[(local_y << 8) | (local_z << 4) | local_x] = STONE
            for local_x, world_y, local_z in local_extra:
                if base_world_y <= world_y < base_world_y + SECTION_SIZE:
                    local_y = world_y - base_world_y
                    ids[(local_y << 8) | (local_z << 4) | local_x] = STONE
            sections.append(ChunkSectionBlocks(section_y=section_y, block_state_ids=ids))

        cache.handle_chunk(ChunkBlocks(chunk_x=chunk_x, chunk_z=chunk_z, sections=sections))

    return cache


def flat_ground(min_x: int, max_x: int, min_z: int, max_z: int, ground_y: int = 0) -> dict[tuple[int, int], int]:
    return {(x, z): ground_y for x in range(min_x, max_x + 1) for z in range(min_z, max_z + 1)}
