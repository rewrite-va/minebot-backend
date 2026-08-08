import pytest

from minebot.bot.inventory import InventoryController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket, so command translation can be tested without a
    running mod.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_give(self, recipient_entity_id, item, quantity):
        self.sent.append(("give", {"recipient_entity_id": recipient_entity_id, "item": item, "quantity": quantity}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _inventory_event(selected_slot: int, slots: list[dict]) -> ModEvent:
    return ModEvent(type="inventory", data={"selected_slot": selected_slot, "slots": slots})


def _inventory(bridge, tracker=None, entity_tracker=None) -> InventoryController:
    return InventoryController(
        bridge,
        tracker if tracker is not None else InventoryTracker(),
        entity_tracker if entity_tracker is not None else EntityTracker(),
    )


@pytest.mark.asyncio
async def test_give_with_explicit_item_and_recipient_sends_give():
    entity_tracker = EntityTracker()
    _add_player(entity_tracker, 7, "Alex")
    bridge = RecordingBridge()
    inventory = _inventory(bridge, entity_tracker=entity_tracker)

    result = await inventory.give(None, "Alex", "diamond", 5)

    assert bridge.sent == [("give", {"recipient_entity_id": 7, "item": "minecraft:diamond", "quantity": 5})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_with_no_recipient_gives_to_the_sender():
    entity_tracker = EntityTracker()
    _add_player(entity_tracker, 7, "Alex")
    bridge = RecordingBridge()
    inventory = _inventory(bridge, entity_tracker=entity_tracker)

    result = await inventory.give("Alex", item="diamond")

    assert bridge.sent == [("give", {"recipient_entity_id": 7, "item": "minecraft:diamond", "quantity": 0})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_with_no_item_gives_the_last_gained_item():
    entity_tracker = EntityTracker()
    _add_player(entity_tracker, 7, "Alex")
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 1}]))
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 2}]))
    bridge = RecordingBridge()
    inventory = _inventory(bridge, tracker=tracker, entity_tracker=entity_tracker)

    result = await inventory.give(None, "Alex")

    assert bridge.sent == [("give", {"recipient_entity_id": 7, "item": "minecraft:bread", "quantity": 0})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_with_no_item_and_nothing_gained_sends_nothing():
    entity_tracker = EntityTracker()
    _add_player(entity_tracker, 7, "Alex")
    bridge = RecordingBridge()
    inventory = _inventory(bridge, entity_tracker=entity_tracker)

    result = await inventory.give(None, "Alex")

    assert bridge.sent == []
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_with_no_recipient_and_no_sender_raises():
    bridge = RecordingBridge()
    inventory = _inventory(bridge)

    with pytest.raises(RuntimeError):
        await inventory.give(None)


@pytest.mark.asyncio
async def test_give_unknown_recipient_sends_nothing_and_returns_a_message():
    bridge = RecordingBridge()
    inventory = _inventory(bridge)

    result = await inventory.give(None, "NobodyHome", "diamond")

    assert bridge.sent == []
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_normalizes_bare_item_id():
    entity_tracker = EntityTracker()
    _add_player(entity_tracker, 7, "Alex")
    bridge = RecordingBridge()
    inventory = _inventory(bridge, entity_tracker=entity_tracker)

    await inventory.give(None, "Alex", "minecraft:diamond", 1)

    assert bridge.sent == [("give", {"recipient_entity_id": 7, "item": "minecraft:diamond", "quantity": 1})]
