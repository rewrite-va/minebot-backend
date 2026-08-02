import uuid

import pytest

from minebot.net.types import ByteWriter
from minebot.protocol.entities import (
    ACTION_ADD_PLAYER,
    ACTION_UPDATE_LATENCY,
    ACTION_UPDATE_LISTED,
    EntityTracker,
    apply_entity_packet,
    parse_absolute_entity_position,
    parse_add_entity,
    parse_move_entity_delta,
    parse_player_info_update,
    parse_remove_entities,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def test_parse_add_entity():
    entity_uuid = uuid.uuid4()
    writer = ByteWriter()
    writer.write_varint(42)  # entity id
    writer.write_uuid(entity_uuid)
    writer.write_varint(100)  # entity type registry id, unused
    writer.write_f64(1.0)
    writer.write_f64(2.0)
    writer.write_f64(3.0)

    entity_id, parsed_uuid, x, y, z = parse_add_entity(writer.getvalue())
    assert entity_id == 42
    assert parsed_uuid == entity_uuid
    assert (x, y, z) == (1.0, 2.0, 3.0)


def test_parse_remove_entities():
    writer = ByteWriter()
    writer.write_varint(3)
    writer.write_varint(1)
    writer.write_varint(2)
    writer.write_varint(3)

    assert parse_remove_entities(writer.getvalue()) == [1, 2, 3]


def test_parse_absolute_entity_position():
    writer = ByteWriter()
    writer.write_varint(7)
    writer.write_f64(10.0)
    writer.write_f64(64.0)
    writer.write_f64(-5.0)

    entity_id, x, y, z = parse_absolute_entity_position(writer.getvalue())
    assert entity_id == 7
    assert (x, y, z) == (10.0, 64.0, -5.0)


def test_parse_move_entity_delta_positive_and_negative():
    writer = ByteWriter()
    writer.write_varint(9)
    writer.write_u16(4096 & 0xFFFF)  # +1.0 block (1 unit = 1/4096 block)
    writer.write_u16((-2048) & 0xFFFF)  # -0.5 block, two's complement in a u16 slot
    writer.write_u16(0)

    entity_id, dx, dy, dz = parse_move_entity_delta(writer.getvalue())
    assert entity_id == 9
    assert dx == pytest.approx(1.0)
    assert dy == pytest.approx(-0.5)
    assert dz == pytest.approx(0.0)


def test_entity_tracker_add_move_remove():
    tracker = EntityTracker()
    entity_uuid = uuid.uuid4()

    tracker.handle_add_entity(1, entity_uuid, 0.0, 0.0, 0.0)
    assert tracker.by_id[1].x == 0.0

    tracker.handle_relative_move(1, 1.0, 0.0, 2.0)
    assert (tracker.by_id[1].x, tracker.by_id[1].z) == (1.0, 2.0)

    tracker.handle_absolute_position(1, 100.0, 64.0, -50.0)
    assert (tracker.by_id[1].x, tracker.by_id[1].y, tracker.by_id[1].z) == (100.0, 64.0, -50.0)

    tracker.handle_remove_entities([1])
    assert 1 not in tracker.by_id


def test_entity_tracker_find_by_name():
    tracker = EntityTracker()
    entity_uuid = uuid.uuid4()

    tracker.handle_add_entity(5, entity_uuid, 1.0, 2.0, 3.0)
    from minebot.protocol.entities import PlayerListEntry

    tracker.handle_player_info([PlayerListEntry(profile_id=entity_uuid, name="Alex")])

    found = tracker.find_by_name("Alex")
    assert found is not None
    assert found.entity_id == 5

    assert tracker.find_by_name("NoOne") is None


def _write_action_bitset(actions: set[int]) -> bytes:
    byte = 0
    for a in actions:
        byte |= 1 << a
    return bytes([byte])


def test_parse_player_info_update_add_player_only():
    entity_uuid = uuid.uuid4()

    writer = ByteWriter()
    writer.write(_write_action_bitset({ACTION_ADD_PLAYER}))
    writer.write_varint(1)  # entry count
    writer.write_uuid(entity_uuid)
    writer.write_utf("Steve")  # name
    writer.write_varint(0)  # zero game profile properties

    entries = parse_player_info_update(writer.getvalue())
    assert len(entries) == 1
    assert entries[0].profile_id == entity_uuid
    assert entries[0].name == "Steve"


def test_parse_player_info_update_skips_other_actions_correctly():
    # ADD_PLAYER + UPDATE_LATENCY + UPDATE_LISTED together, two entries --
    # regression coverage for correct positional skipping of non-name
    # actions so a second entry's fields don't get misaligned.
    uuid1, uuid2 = uuid.uuid4(), uuid.uuid4()

    writer = ByteWriter()
    writer.write(_write_action_bitset({ACTION_ADD_PLAYER, ACTION_UPDATE_LATENCY, ACTION_UPDATE_LISTED}))
    writer.write_varint(2)

    writer.write_uuid(uuid1)
    writer.write_utf("Alex")
    writer.write_varint(0)
    writer.write_varint(50)  # latency
    writer.write_bool(True)  # listed

    writer.write_uuid(uuid2)
    writer.write_utf("Bob")
    writer.write_varint(0)
    writer.write_varint(30)
    writer.write_bool(False)

    entries = parse_player_info_update(writer.getvalue())
    assert len(entries) == 2
    assert entries[0].name == "Alex"
    assert entries[1].name == "Bob"


def test_apply_entity_packet_routes_add_entity():
    tracker = EntityTracker()
    entity_uuid = uuid.uuid4()
    writer = ByteWriter()
    writer.write_varint(3)
    writer.write_uuid(entity_uuid)
    writer.write_varint(0)
    writer.write_f64(5.0)
    writer.write_f64(6.0)
    writer.write_f64(7.0)

    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_ADD_ENTITY")
    apply_entity_packet(tracker, packet_id, writer.getvalue())

    assert tracker.by_id[3].uuid == entity_uuid
    assert (tracker.by_id[3].x, tracker.by_id[3].y, tracker.by_id[3].z) == (5.0, 6.0, 7.0)
