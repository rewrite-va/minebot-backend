import asyncio
import math
import uuid

import pytest

from minebot.bot.movement import MovementController
from minebot.net.connection import Connection
from minebot.net.types import ByteReader
from minebot.protocol.entities import EntityTracker, PlayerListEntry
from minebot.protocol.movement import PlayerPositionSync
from minebot.protocol.registry import REGISTRY

STATE = "play"


class RecordingConnection:
    """Stands in for a real Connection: records every (packet_id, data)
    sent instead of touching a socket, so movement math can be tested
    without a fake TCP server.
    """

    def __init__(self):
        self.sent: list[tuple[int, bytes]] = []

    async def send_packet(self, packet_id: int, data: bytes) -> None:
        self.sent.append((packet_id, data))


def _last_move(conn: RecordingConnection):
    move_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_MOVE_PLAYER_POS_ROT")
    packet_id, data = conn.sent[-1]
    assert packet_id == move_id
    reader = ByteReader(data)
    return {
        "x": reader.read_f64(),
        "y": reader.read_f64(),
        "z": reader.read_f64(),
        "yaw": reader.read_f32(),
        "pitch": reader.read_f32(),
    }


@pytest.mark.asyncio
async def test_forward_without_known_position_raises():
    movement = MovementController(EntityTracker())
    with pytest.raises(RuntimeError):
        await movement.forward(RecordingConnection(), None)


@pytest.mark.asyncio
async def test_sync_from_position_packet_sets_absolute_position():
    movement = MovementController(EntityTracker())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=10.0, y=64.0, z=-5.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    assert (movement.x, movement.y, movement.z) == (10.0, 64.0, -5.0)
    assert movement.has_position is True


@pytest.mark.asyncio
async def test_forward_at_yaw_zero_moves_north_positive_z():
    # yaw 0 faces +Z per vanilla convention.
    movement = MovementController(EntityTracker())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.forward(conn, None, distance=2.0)

    result = _last_move(conn)
    assert result["x"] == pytest.approx(0.0, abs=1e-6)
    assert result["z"] == pytest.approx(2.0)
    assert movement.z == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_backward_is_opposite_of_forward():
    movement = MovementController(EntityTracker())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=45.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.forward(conn, None, distance=1.0)
    forward_pos = (movement.x, movement.z)

    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=2, x=0.0, y=64.0, z=0.0, yaw=45.0, pitch=0.0, relatives=0)
    )
    await movement.backward(conn, None, distance=1.0)
    backward_pos = (movement.x, movement.z)

    assert forward_pos[0] == pytest.approx(-backward_pos[0], abs=1e-6)
    assert forward_pos[1] == pytest.approx(-backward_pos[1], abs=1e-6)


@pytest.mark.asyncio
async def test_strafe_left_and_right_are_opposite():
    movement = MovementController(EntityTracker())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.strafe_left(conn, None, distance=1.0)
    left_pos = (movement.x, movement.z)

    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=2, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    await movement.strafe_right(conn, None, distance=1.0)
    right_pos = (movement.x, movement.z)

    assert left_pos[0] == pytest.approx(-right_pos[0], abs=1e-6)
    assert left_pos[1] == pytest.approx(-right_pos[1], abs=1e-6)


@pytest.mark.asyncio
async def test_follow_by_explicit_name_moves_toward_target_and_stop_cancels():
    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 10.0, 64.0, 0.0)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    await asyncio.sleep(0.6)  # let at least one follow-loop tick fire
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    # Should have moved toward +x (target is at x=10) without overshooting past it.
    assert 0.0 < result["x"] <= 10.0

    sent_count_after_stop = len(conn.sent)
    await asyncio.sleep(0.6)
    assert len(conn.sent) == sent_count_after_stop  # no further movement after stop


@pytest.mark.asyncio
async def test_follow_with_no_name_follows_the_chat_sender():
    # "!follow" with no argument should follow whoever typed it in chat.
    tracker = EntityTracker()
    sender_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, sender_uuid, 5.0, 64.0, 0.0)
    # Deliberately no tab-list/name mapping registered -- following by
    # sender uuid must not depend on the name lookup succeeding.

    movement = MovementController(tracker)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, sender_uuid)
    await asyncio.sleep(0.6)
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    assert 0.0 < result["x"] <= 5.0


@pytest.mark.asyncio
async def test_follow_with_no_name_and_no_sender_raises():
    movement = MovementController(EntityTracker())
    with pytest.raises(RuntimeError):
        await movement.follow(RecordingConnection(), None)
