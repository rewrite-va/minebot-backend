"""Unit tests for the schematic-placement helpers in minebot/testing/
actions.py -- specifically _fill_runs, the pure row-merging logic that
turns a Schematic's individual blocks into /fill-able runs, and
reset_to_idle, which just needs a fake bridge to record what it sent.
place_schematic/clear_schematic themselves need a real ModBridge and are
exercised by the in-game integration suite instead (tests/integration/),
not here.
"""

from __future__ import annotations

import pytest

from minebot.testing import actions
from minebot.testing.actions import _fill_runs
from minebot.testing.litematic import Schematic, SchematicBlock
from minebot.testing.runner import TestContext


def test_fill_runs_merges_contiguous_same_block_row():
    schematic = Schematic(
        size_x=3, size_y=1, size_z=1,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=2, y=0, z=0, block="minecraft:stone"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [(0, 0, 0, 2, 0, 0, "minecraft:stone")]


def test_fill_runs_splits_on_block_type_change_and_gap():
    schematic = Schematic(
        size_x=4, size_y=1, size_z=1,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:dirt"),
            # x=2 is missing (a gap) -- must not be merged with x=3.
            SchematicBlock(x=3, y=0, z=0, block="minecraft:dirt"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [
        (0, 0, 0, 0, 0, 0, "minecraft:stone"),
        (1, 0, 0, 1, 0, 0, "minecraft:dirt"),
        (3, 0, 0, 3, 0, 0, "minecraft:dirt"),
    ]


def test_fill_runs_keeps_separate_rows_separate():
    schematic = Schematic(
        size_x=2, size_y=1, size_z=2,
        blocks=[
            SchematicBlock(x=0, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=0, block="minecraft:stone"),
            SchematicBlock(x=0, y=0, z=1, block="minecraft:stone"),
            SchematicBlock(x=1, y=0, z=1, block="minecraft:stone"),
        ],
    )

    runs = _fill_runs(schematic)

    assert runs == [
        (0, 0, 0, 1, 0, 0, "minecraft:stone"),
        (0, 0, 1, 1, 0, 1, "minecraft:stone"),
    ]


def test_fill_runs_empty_schematic():
    assert _fill_runs(Schematic(size_x=1, size_y=1, size_z=1, blocks=[])) == []


class _FakeBridge:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_stop(self) -> None:
        self.sent.append(("stop", {}))


@pytest.mark.asyncio
async def test_reset_to_idle_sends_stop():
    bridge = _FakeBridge()
    ctx = TestContext(bridge=bridge, self_position=None, tracker=None)

    await actions.reset_to_idle(ctx)

    assert bridge.sent == [("stop", {})]
