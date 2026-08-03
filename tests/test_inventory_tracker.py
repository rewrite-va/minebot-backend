from minebot.bridge.client import ModEvent
from minebot.bridge.inventory import InventoryTracker


def _inventory_event(selected_slot: int, slots: list[dict]) -> ModEvent:
    return ModEvent(type="inventory", data={"selected_slot": selected_slot, "slots": slots})


def test_snapshot_replaces_previous_state_wholesale():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))
    tracker.handle_event(_inventory_event(1, [{"slot": 1, "item": "minecraft:stone", "count": 10}]))

    assert tracker.selected_slot == 1
    assert tracker.slot(0) is None  # bread is gone -- snapshot, not a diff
    assert tracker.slot(1).item == "minecraft:stone"


def test_slot_carries_damage_when_present():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [
        {"slot": 0, "item": "minecraft:diamond_pickaxe", "count": 1, "damage": 3, "max_damage": 1561},
    ]))

    entry = tracker.slot(0)
    assert entry.damage == 3
    assert entry.max_damage == 1561


def test_slot_omits_damage_for_undamaged_items():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))

    entry = tracker.slot(0)
    assert entry.damage is None
    assert entry.max_damage is None


def test_find_by_item_returns_first_matching_slot():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [
        {"slot": 3, "item": "minecraft:bread", "count": 2},
        {"slot": 8, "item": "minecraft:bread", "count": 1},
    ]))

    entry = tracker.find_by_item("minecraft:bread")
    assert entry.slot == 3


def test_find_by_item_returns_none_when_not_carried():
    tracker = InventoryTracker()
    assert tracker.find_by_item("minecraft:bread") is None


def test_count_of_sums_across_slots():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [
        {"slot": 3, "item": "minecraft:bread", "count": 2},
        {"slot": 8, "item": "minecraft:bread", "count": 1},
        {"slot": 9, "item": "minecraft:stone", "count": 64},
    ]))

    assert tracker.count_of("minecraft:bread") == 3
    assert tracker.count_of("minecraft:stone") == 64
    assert tracker.count_of("minecraft:diamond") == 0


def test_slots_returns_empty_list_before_any_event():
    tracker = InventoryTracker()
    assert tracker.slots() == []
    assert tracker.selected_slot is None


def test_non_inventory_events_are_ignored():
    tracker = InventoryTracker()
    tracker.handle_event(ModEvent(type="chat", data={"text": "hi"}))
    assert tracker.slots() == []
