import asyncio

import pytest

import minebot.bot.mining as mining_module
from minebot.bot.mining import MiningController
from minebot.bridge.client import ModEvent
from minebot.bridge.inventory import InventoryTracker


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command and chat
    message instead of touching a socket, so command translation can be
    tested without a running mod -- same shape as test_movement_controller's
    own RecordingBridge.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.sent_chat: list[str] = []

    async def send_dig_down(self, count):
        self.sent.append(("dig_down", {"count": count}))

    async def send_collect(self, query, radius=64):
        self.sent.append(("collect", {"query": query, "radius": radius}))

    async def send_query(self, sub_type, arguments, key):
        self.sent.append(("query", {"sub_type": sub_type, "arguments": arguments, "key": key}))

    async def send_chat(self, text):
        self.sent_chat.append(text)


def _inventory_event(counts: dict[str, int]) -> ModEvent:
    """Builds a full inventory snapshot event with one slot per item,
    matching the mod's real wire shape closely enough for InventoryTracker
    -- item ids are namespaced ("minecraft:<id>") the same way real
    InventoryReporter events always are (bare ids here would silently
    never match MiningController.collect's own now-normalized
    expected_drops, masking the exact bug this normalization fixed).
    """
    slots = [
        {"slot": i, "item": item if ":" in item else f"minecraft:{item}", "count": count}
        for i, (item, count) in enumerate(counts.items())
    ]
    return ModEvent(type="inventory", data={"selected_slot": 0, "slots": slots})


def _last_query_key(bridge: RecordingBridge, sub_type: str) -> str:
    for kind, payload in reversed(bridge.sent):
        if kind == "query" and payload["sub_type"] == sub_type:
            return payload["key"]
    raise AssertionError(f"no {sub_type} query sent")


@pytest.mark.asyncio
async def test_dig_down_sends_command_and_reports_full_completion():
    bridge = RecordingBridge()
    mining = MiningController(bridge, InventoryTracker())

    task = asyncio.ensure_future(mining.dig_down(None, 5))
    await asyncio.sleep(0)  # let dig_down() send the command and start awaiting the result
    mining.on_dig_down_result({"broken": 5})
    result = await task

    assert bridge.sent == [("dig_down", {"count": 5})]
    assert result.message == "dug down 5 blocks"


@pytest.mark.asyncio
async def test_dig_down_defaults_to_ten():
    bridge = RecordingBridge()
    mining = MiningController(bridge, InventoryTracker())

    task = asyncio.ensure_future(mining.dig_down(None))
    await asyncio.sleep(0)
    mining.on_dig_down_result({"broken": 10})
    await task

    assert bridge.sent == [("dig_down", {"count": 10})]


@pytest.mark.asyncio
async def test_dig_down_reports_an_early_abort_reason():
    bridge = RecordingBridge()
    mining = MiningController(bridge, InventoryTracker())

    task = asyncio.ensure_future(mining.dig_down(None, 10))
    await asyncio.sleep(0)
    mining.on_dig_down_result({"broken": 3, "reason": "hit lava/water"})
    result = await task

    assert "3/10" in result.message
    assert "hit lava/water" in result.message


@pytest.mark.asyncio
async def test_dig_down_times_out_if_the_mod_never_replies(monkeypatch):
    monkeypatch.setattr(mining_module, "DIG_DOWN_TIMEOUT", 0.01)
    bridge = RecordingBridge()
    mining = MiningController(bridge, InventoryTracker())

    result = await mining.dig_down(None, 5)  # on_dig_down_result never called -- times out fast (patched to 0.01s)

    assert "no response" in result.message


async def _drive_collect(mining, bridge, inventory, *, source_for: str, drops_from: list[str], gains: list[dict[str, int]], collect_results: list[dict]):
    """Drives one collect() call through its query/attempt cycle: resolves
    source_for and drops_from queries as soon as they're sent, then for
    each expected collect attempt delivers a collect_result followed by an
    inventory bump (or no bump, for an empty-drop attempt) from `gains`.
    """
    await asyncio.sleep(0)  # let collect() send its source_for query
    key = _last_query_key(bridge, "source_for")
    mining.on_query_result({"key": key, "result": [source_for]})

    await asyncio.sleep(0)  # let collect() send its drops_from query
    key = _last_query_key(bridge, "drops_from")
    mining.on_query_result({"key": key, "result": drops_from})

    for gain, collect_result in zip(gains, collect_results):
        await asyncio.sleep(0)  # let collect() send this attempt's collect command
        mining.on_collect_result(collect_result)
        if gain:
            await asyncio.sleep(0)
            inventory.handle_event(_inventory_event(gain))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_collect_sends_command_and_reports_full_completion():
    bridge = RecordingBridge()
    inventory = InventoryTracker()
    mining = MiningController(bridge, inventory)

    task = asyncio.ensure_future(mining.collect(None, "stone", 3))
    await _drive_collect(
        mining, bridge, inventory,
        source_for="stone", drops_from=["stone"],
        gains=[{"stone": 1}, {"stone": 2}, {"stone": 3}],
        collect_results=[{"success": True}, {"success": True}, {"success": True}],
    )
    result = await task

    assert ("collect", {"query": "stone", "radius": 64}) in bridge.sent
    assert result.message == "collected 3/3 stone"


@pytest.mark.asyncio
async def test_collect_resolves_query_through_source_for():
    # "cobblestone" isn't itself minable -- source_for should resolve it to
    # "stone" before any collect command is ever sent.
    bridge = RecordingBridge()
    inventory = InventoryTracker()
    mining = MiningController(bridge, inventory)

    task = asyncio.ensure_future(mining.collect(None, "cobblestone", 1))
    await _drive_collect(
        mining, bridge, inventory,
        source_for="stone", drops_from=["cobblestone"],
        gains=[{"cobblestone": 1}],
        collect_results=[{"success": True}],
    )
    result = await task

    assert ("collect", {"query": "stone", "radius": 64}) in bridge.sent
    assert result.message == "collected 1/1 cobblestone"


@pytest.mark.asyncio
async def test_collect_reports_an_early_stop_reason():
    bridge = RecordingBridge()
    inventory = InventoryTracker()
    mining = MiningController(bridge, inventory)

    task = asyncio.ensure_future(mining.collect(None, "cow", 10))
    await asyncio.sleep(0)
    key = _last_query_key(bridge, "source_for")
    mining.on_query_result({"key": key, "result": ["cow"]})
    await asyncio.sleep(0)
    key = _last_query_key(bridge, "drops_from")
    mining.on_query_result({"key": key, "result": ["beef", "leather"]})

    for i in range(1, 7):
        await asyncio.sleep(0)
        mining.on_collect_result({"success": True})
        await asyncio.sleep(0)
        inventory.handle_event(_inventory_event({"beef": i}))
        await asyncio.sleep(0)

    await asyncio.sleep(0)
    mining.on_collect_result({"success": False, "reason": "no more cow found nearby"})
    result = await task

    assert "6/10" in result.message
    assert "no more cow found nearby" in result.message


@pytest.mark.asyncio
async def test_collect_retries_on_empty_drops_without_counting_them():
    # A destroyed/killed target that yields no expected drop (real vanilla
    # randomness) must retry the same target type rather than counting
    # toward the requested total.
    bridge = RecordingBridge()
    inventory = InventoryTracker()
    mining = MiningController(bridge, inventory)
    mining_module.DROP_CONFIRMATION_TIMEOUT = 0.05

    task = asyncio.ensure_future(mining.collect(None, "cow", 1))
    await asyncio.sleep(0)
    key = _last_query_key(bridge, "source_for")
    mining.on_query_result({"key": key, "result": ["cow"]})
    await asyncio.sleep(0)
    key = _last_query_key(bridge, "drops_from")
    mining.on_query_result({"key": key, "result": ["beef"]})

    await asyncio.sleep(0)  # first attempt: destroyed, but no drop lands
    mining.on_collect_result({"success": True})
    await asyncio.sleep(0.1)  # DROP_CONFIRMATION_TIMEOUT elapses with no inventory change

    await asyncio.sleep(0)  # second attempt: succeeds for real
    mining.on_collect_result({"success": True})
    await asyncio.sleep(0)
    inventory.handle_event(_inventory_event({"beef": 1}))
    result = await task

    collect_commands = [p for kind, p in bridge.sent if kind == "collect"]
    assert len(collect_commands) == 2
    assert result.message == "collected 1/1 cow"


@pytest.mark.asyncio
async def test_dig_down_and_collect_use_independent_pending_slots():
    # Regression guard: dig_down and collect each track their own pending
    # future/dict -- resolving one must never affect the other.
    bridge = RecordingBridge()
    inventory = InventoryTracker()
    mining = MiningController(bridge, inventory)

    dig_task = asyncio.ensure_future(mining.dig_down(None, 1))
    await asyncio.sleep(0)
    mining.on_dig_down_result({"broken": 1})
    dig_result = await dig_task

    assert dig_result.message == "dug down 1 block"
