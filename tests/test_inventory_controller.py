import pytest

from minebot.bot.inventory import GIVE_STOP_DISTANCE, InventoryController
from minebot.bridge.client import ModEvent
from minebot.bridge.entities import EntityTracker
from minebot.bridge.inventory import InventoryTracker
from minebot.bridge.self_position import SelfPositionTracker


class RecordingBridge:
    """Stands in for a real ModBridge -- records every sent command
    instead of touching a socket, matching test_movement_controller.py's
    RecordingBridge shape. Handlers now return ActionResult instead of
    calling send_chat themselves (see actions/types.py), so there's no
    `chats` list here anymore -- tests assert on the returned
    ActionResult's message instead.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_equip(self, slot):
        self.sent.append(("equip", {"slot": slot}))

    async def send_drop(self, slot, count):
        self.sent.append(("drop", {"slot": slot, "count": count}))

    async def send_give(self, entity_id, slot, count, stop_distance=2.0):
        self.sent.append(("give", {"entity_id": entity_id, "slot": slot, "count": count, "stop_distance": stop_distance}))


def _inventory_with(inventory: InventoryTracker, slot: int, item: str, count: int) -> None:
    inventory.handle_event(ModEvent(type="inventory", data={"selected_slot": 0, "slots": [{"slot": slot, "item": item, "count": count}]}))


def _add_player(tracker: EntityTracker, entity_id: int, name: str, x=0.0, y=0.0, z=0.0) -> None:
    tracker.handle_event(ModEvent(type="entity", data={"action": "add", "id": entity_id, "name": name, "x": x, "y": y, "z": z}))


def _at(self_position: SelfPositionTracker, x=0.0, y=0.0, z=0.0) -> None:
    self_position.handle_event(ModEvent(type="position", data={"x": x, "y": y, "z": z, "yaw": 0.0, "pitch": 0.0}))


def _controller(bridge, inventory=None, tracker=None, self_position=None) -> InventoryController:
    return InventoryController(
        bridge,
        inventory if inventory is not None else InventoryTracker(),
        tracker if tracker is not None else EntityTracker(),
        self_position if self_position is not None else SelfPositionTracker(),
    )


@pytest.mark.asyncio
async def test_inventory_report_lists_carried_items():
    inventory = InventoryTracker()
    _inventory_with(inventory, 0, "minecraft:bread", 5)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.inventory_report(None)

    assert result.message == "inventory: 5x bread"


@pytest.mark.asyncio
async def test_inventory_report_when_empty():
    bridge = RecordingBridge()
    controller = _controller(bridge)

    result = await controller.inventory_report(None)

    assert result.message == "my inventory is empty"


@pytest.mark.asyncio
async def test_equip_resolves_bare_item_name_to_slot():
    inventory = InventoryTracker()
    _inventory_with(inventory, 10, "minecraft:diamond_sword", 1)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.equip(None, "diamond_sword")

    assert bridge.sent == [("equip", {"slot": 10})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_equip_accepts_already_namespaced_item_name():
    inventory = InventoryTracker()
    _inventory_with(inventory, 10, "minecraft:diamond_sword", 1)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.equip(None, "minecraft:diamond_sword")

    assert bridge.sent == [("equip", {"slot": 10})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_equip_unknown_item_sends_no_command():
    bridge = RecordingBridge()
    controller = _controller(bridge)

    result = await controller.equip(None, "diamond_sword")

    assert bridge.sent == []
    assert result.message == "I don't have any diamond_sword"


@pytest.mark.asyncio
async def test_drop_defaults_to_one():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:cobblestone", 64)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.drop(None, "cobblestone")

    assert bridge.sent == [("drop", {"slot": 3, "count": 1})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_drop_with_explicit_count():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:cobblestone", 64)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.drop(None, "cobblestone", 32)

    assert bridge.sent == [("drop", {"slot": 3, "count": 32})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_give_drops_the_most_recently_gained_item_to_a_named_player():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)  # first snapshot -- counts as a gain from 0
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "Alex")

    assert bridge.sent == [("give", {"entity_id": 7, "slot": 3, "count": 5, "stop_distance": GIVE_STOP_DISTANCE})]
    assert "bread" in result.message


@pytest.mark.asyncio
async def test_give_with_no_player_name_gives_to_the_closest_player():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    tracker = EntityTracker()
    _add_player(tracker, 7, "Far", x=100.0)
    _add_player(tracker, 8, "Near", x=1.0)
    self_position = SelfPositionTracker()
    _at(self_position, x=0.0)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker, self_position)

    result = await controller.give(None)

    assert bridge.sent == [("give", {"entity_id": 8, "slot": 3, "count": 5, "stop_distance": GIVE_STOP_DISTANCE})]
    assert "Near" in result.message


@pytest.mark.asyncio
async def test_give_with_no_players_nearby_and_no_name_sends_nothing():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.give(None)

    assert bridge.sent == []
    assert "nearby" in result.message


@pytest.mark.asyncio
async def test_give_with_no_position_known_and_no_name_sends_nothing():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)  # self_position never fed a position event

    result = await controller.give(None)

    assert bridge.sent == []


@pytest.mark.asyncio
async def test_give_unknown_player_name_falls_back_to_treating_it_as_an_item():
    # "NobodyHome" isn't a currently-visible player, so the single-bare-
    # arg form falls back to treating it as an item name (same resolution
    # order !find uses for entity-vs-block) -- not carrying any item by
    # that name either, so this reports the item-not-carried message, not
    # a player-not-visible one.
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory)

    result = await controller.give(None, "NobodyHome")

    assert bridge.sent == []
    assert result.message == "I don't have any NobodyHome"


@pytest.mark.asyncio
async def test_give_with_explicit_item_and_player_gives_that_item():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:stick", 5)
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "stick", "Alex")

    assert bridge.sent == [("give", {"entity_id": 7, "slot": 3, "count": 5, "stop_distance": GIVE_STOP_DISTANCE})]
    assert "stick" in result.message


@pytest.mark.asyncio
async def test_give_with_explicit_item_not_carried_sends_no_command():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, tracker=tracker)

    result = await controller.give(None, "stick", "Alex")

    assert bridge.sent == []
    assert result.message == "I don't have any stick"


@pytest.mark.asyncio
async def test_give_single_arg_that_is_a_known_player_gives_the_gained_item_to_them():
    # A single bare arg that DOES match a currently-visible player is
    # resolved as the recipient, not an item -- e.g. "!give riterite"
    # gives back whatever was gained, to riterite specifically, rather
    # than being interpreted as "give the item named riterite".
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)
    tracker = EntityTracker()
    _add_player(tracker, 7, "riterite")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "riterite")

    assert bridge.sent == [("give", {"entity_id": 7, "slot": 3, "count": 5, "stop_distance": GIVE_STOP_DISTANCE})]
    assert "bread" in result.message


@pytest.mark.asyncio
async def test_give_with_nothing_recently_gained_sends_no_command():
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, tracker=tracker)  # InventoryTracker never received an inventory event

    result = await controller.give(None, "Alex")

    assert bridge.sent == []
    assert "haven't picked up" in result.message


@pytest.mark.asyncio
async def test_give_reports_nothing_gained_once_the_item_was_since_used_up():
    # gained_items() only ever reflects the transition between the *last
    # two* snapshots, not history further back -- once bread's own most
    # recent transition is a decrease (used/dropped/crafted away), it no
    # longer counts as "recently gained" even though it was a moment ago.
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)  # gained
    # ...then dropped/used/crafted away -- empty slots aren't listed at
    # all (see InventoryReporter: "only non-empty slots listed"), so the
    # next real snapshot would simply omit slot 3, not report count=0.
    inventory.handle_event(ModEvent(type="inventory", data={"selected_slot": 0, "slots": []}))
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "Alex")

    assert bridge.sent == []
    assert "haven't picked up" in result.message


@pytest.mark.asyncio
async def test_give_when_the_gained_item_is_used_up_before_give_is_typed_sends_no_command():
    # A real gap the class docstring calls out: gained_items() reflects
    # the last snapshot *transition*, not whether the item is still on
    # hand right now. If the very next tick after a gain still reports it
    # present (not yet re-used), but it's gone by the time !give actually
    # runs (e.g. FoodEater ate it in between), find_by_item comes back
    # empty even though gained_items() still names it.
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)  # gained, still the latest transition
    inventory._by_slot.pop(3)  # simulates FoodEater eating it between the gain and !give, with no new snapshot yet
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "Alex")

    assert bridge.sent == []
    assert "don't have any bread" in result.message


@pytest.mark.asyncio
async def test_give_only_tracks_items_that_increased_not_every_snapshot():
    inventory = InventoryTracker()
    _inventory_with(inventory, 3, "minecraft:bread", 5)  # gained bread
    inventory.handle_event(ModEvent(type="inventory", data={
        "selected_slot": 0,
        "slots": [
            {"slot": 3, "item": "minecraft:bread", "count": 4},  # bread went DOWN (eaten/dropped)
            {"slot": 5, "item": "minecraft:cobblestone", "count": 10},  # cobblestone appeared
        ],
    }))
    tracker = EntityTracker()
    _add_player(tracker, 7, "Alex")
    bridge = RecordingBridge()
    controller = _controller(bridge, inventory, tracker)

    result = await controller.give(None, "Alex")

    assert bridge.sent == [("give", {"entity_id": 7, "slot": 5, "count": 10, "stop_distance": GIVE_STOP_DISTANCE})]
    assert "cobblestone" in result.message
