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


def test_gained_items_treats_the_very_first_snapshot_as_pure_baseline():
    # The very first `inventory` event this tracker ever sees has nothing
    # genuine to diff against -- it's establishing what the bot already
    # carries (InventoryReporter.forceNextBroadcast sends one immediately
    # on connect specifically for this), not one enormous simultaneous
    # pickup. An earlier version diffed it against an empty baseline
    # instead, so everything in it read as "gained" -- confirmed live this
    # flooded chat with one "I got a ..." announcement per already-carried
    # item on every mod (re)connect, badly enough to get the bot kicked
    # for spamming.
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))

    assert tracker.gained_items() == []
    assert tracker.last_gained_item is None


def test_gained_items_detects_a_real_gain_after_the_first_snapshot():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}, {"slot": 1, "item": "minecraft:stone", "count": 1}]))

    assert tracker.gained_items() == ["minecraft:stone"]
    assert tracker.last_gained_item == "minecraft:stone"


def test_gained_items_detects_a_new_item_appearing():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))
    tracker.handle_event(_inventory_event(0, [
        {"slot": 0, "item": "minecraft:bread", "count": 5},
        {"slot": 1, "item": "minecraft:phantom_membrane", "count": 1},
    ]))

    assert tracker.gained_items() == ["minecraft:phantom_membrane"]
    assert tracker.last_gained_item == "minecraft:phantom_membrane"


def test_gained_items_ignores_items_that_were_already_carried_and_unchanged():
    # Regression test: a live report of !give returning a golden_carrot
    # instead of a just-received phantom_membrane -- the bot already
    # carried a large stack of golden carrots (unchanged across the two
    # snapshots being compared), and the old "biggest total increase"
    # heuristic mistakenly attributed that pre-existing stack as the
    # latest gain since it outsized the freshly-gained single membrane.
    # A real per-item diff shows zero change for an item present
    # identically in both snapshots, regardless of its absolute size.
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:golden_carrot", "count": 20}]))
    tracker.handle_event(_inventory_event(0, [
        {"slot": 0, "item": "minecraft:golden_carrot", "count": 20},  # unchanged
        {"slot": 1, "item": "minecraft:phantom_membrane", "count": 1},  # just received
    ]))

    assert tracker.gained_items() == ["minecraft:phantom_membrane"]


def test_gained_items_ignores_decreases():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 4}]))

    assert tracker.gained_items() == []


def test_gained_items_can_report_more_than_one_item():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 1}]))
    tracker.handle_event(_inventory_event(0, [
        {"slot": 0, "item": "minecraft:bread", "count": 1},
        {"slot": 1, "item": "minecraft:stick", "count": 3},
        {"slot": 2, "item": "minecraft:stone", "count": 10},
    ]))

    assert set(tracker.gained_items()) == {"minecraft:stick", "minecraft:stone"}


def test_gained_items_only_reflects_the_most_recent_transition():
    tracker = InventoryTracker()
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))  # gained
    tracker.handle_event(_inventory_event(0, [{"slot": 0, "item": "minecraft:bread", "count": 5}]))  # unchanged now

    assert tracker.gained_items() == []
