import pytest

from minebot.bot.inventory import GIVE_STOP_DISTANCE, InventoryController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker


class RecordingBridge:
    """Stands in for a real ModBridge -- records every sent command and
    every chat message instead of touching a socket, matching
    test_movement_controller.py's RecordingBridge shape.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.chats: list[str] = []

    async def send_equip(self, slot):
        self.sent.append(("equip", {"slot": slot}))

    async def send_drop(self, slot, count):
        self.sent.append(("drop", {"slot": slot, "count": count}))

    async def send_give(self, entity_id, slot, count, stop_distance=2.0):
        self.sent.append(("give", {"entity_id": entity_id, "slot": slot, "count": count, "stop_distance": stop_distance}))

    async def send_chat(self, text):
        self.chats.append(text)


def _inventory_with(inventory: InventoryTracker, slot: int, item: str, count: int) -> None:
    inventory.handle_event(ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": slot, "item": item, "count": count}]}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": 0.0, "y": 0.0, "z": 0.0}))


@pytest.mark.asyncio
async def test_inventory_report_lists_carried_items():
    inventory = InventoryTracker()
    _inventory_with(inventory, 0, "minecraft:bread", 5)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.inventory_report(None)

    assert bridge.chats == ["inventory: 5x bread"]


@pytest.mark.asyncio
async def test_inventory_report_when_empty():
    bridge = RecordingBridge()
    controller = InventoryController(bridge, InventoryTracker(), EntityTracker())

    await controller.inventory_report(None)

    assert bridge.chats == ["my inventory is empty"]


@pytest.mark.asyncio
async def test_equip_resolves_bare_item_name_to_slot():
    inventory = InventoryTracker()
    _inventory_with(inventory, 10, "minecraft:diamond_sword", 1)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.equip(None, "diamond_sword")

    assert bridge.sent == [("equip", {"slot": 10})]


@pytest.mark.asyncio
async def test_equip_accepts_already_namespaced_item_name():
    inventory = InventoryTracker()
    _inventory_with(inventory, 10, "minecraft:diamond_sword", 1)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.equip(None, "minecraft:diamond_sword")

    assert bridge.sent == [("equip", {"slot": 10})]


@pytest.mark.asyncio
async def test_equip_unknown_item_sends_no_command():
    bridge = RecordingBridge()
    controller = InventoryController(bridge, InventoryTracker(), EntityTracker())

    await controller.equip(None, "diamond_sword")

    assert bridge.sent == []
    assert bridge.chats == ["I don't have any diamond_sword"]


@pytest.mark.asyncio
async def test_drop_defaults_to_one():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:cobblestone", 64)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.drop(None, "cobblestone")

    assert bridge.sent == [("drop", {"slot": 3, "count": 1})]


@pytest.mark.asyncio
async def test_drop_with_explicit_count():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:cobblestone", 64)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.drop(None, "cobblestone", 32)

    assert bridge.sent == [("drop", {"slot": 3, "count": 32})]


@pytest.mark.asyncio
async def test_give_walks_to_recipient_and_drops():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, tracker)

    await controller.give(None, "Alex", "bread", 2)

    assert bridge.sent == [("give", {"entity_id": 7, "slot": 3, "count": 2, "stop_distance": GIVE_STOP_DISTANCE})]


@pytest.mark.asyncio
async def test_give_unknown_player_sends_no_command():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    bridge = RecordingBridge()
    controller = InventoryController(bridge, inventory, EntityTracker())

    await controller.give(None, "NobodyHome", "bread")

    assert bridge.sent == []
    assert bridge.chats == ["I can't see NobodyHome"]


@pytest.mark.asyncio
async def test_give_unknown_item_sends_no_command():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = InventoryController(bridge, InventoryTracker(), tracker)

    await controller.give(None, "Alex", "bread")

    assert bridge.sent == []
    assert bridge.chats == ["I don't have any bread"]
