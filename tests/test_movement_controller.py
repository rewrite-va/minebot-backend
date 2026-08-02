import asyncio
import math
import uuid

import pytest

from minebot.bot.movement import FOLLOW_STEP_INTERVAL_SECONDS, MovementController
from minebot.net.connection import Connection
from minebot.net.types import ByteReader
from minebot.protocol.chunk_blocks import ChunkBlockCache
from minebot.protocol.chunks import ChunkHeightmapCache
from minebot.protocol.entities import EntityTracker, PlayerListEntry
from minebot.protocol.movement import PlayerPositionSync
from minebot.protocol.registry import REGISTRY
from pathfinding_fixtures import build_world, flat_ground

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
    movement = MovementController(EntityTracker(), ChunkBlockCache())
    with pytest.raises(RuntimeError):
        await movement.forward(RecordingConnection(), None)


@pytest.mark.asyncio
async def test_sync_from_position_packet_sets_absolute_position():
    movement = MovementController(EntityTracker(), ChunkBlockCache())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=10.0, y=64.0, z=-5.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    assert (movement.x, movement.y, movement.z) == (10.0, 64.0, -5.0)
    assert movement.has_position is True


@pytest.mark.asyncio
async def test_forward_at_yaw_zero_moves_north_positive_z():
    # yaw 0 faces +Z per vanilla convention.
    movement = MovementController(EntityTracker(), ChunkBlockCache())
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
    movement = MovementController(EntityTracker(), ChunkBlockCache())
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
    movement = MovementController(EntityTracker(), ChunkBlockCache())
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

    movement = MovementController(tracker, ChunkBlockCache())
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

    movement = MovementController(tracker, ChunkBlockCache())
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
    movement = MovementController(EntityTracker(), ChunkBlockCache())
    with pytest.raises(RuntimeError):
        await movement.follow(RecordingConnection(), None)


@pytest.mark.asyncio
async def test_follow_ramps_toward_target_tracked_y():
    # Regression test for the "floating in the air when the target is on
    # lower ground" bug found in live testing: the follow loop should
    # approach the target's own tracked Y (not just walk in the (x,z)
    # plane at whatever Y it started at), ramping rather than teleporting.
    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 5.0, 0.0, 0.0)  # target is at Y=0
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, ChunkBlockCache())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=999.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    await asyncio.sleep(0.3)  # a couple of ticks
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    # Should be moving toward Y=0 but not have teleported straight there
    # from Y=999 in a couple of ticks (ramped by FOLLOW_MAX_VERTICAL_STEP
    # each tick, not snapped).
    assert result["y"] < 999.0
    assert result["y"] > 990.0


@pytest.mark.asyncio
async def test_follow_matches_target_y_under_a_roof_not_the_heightmap():
    # Regression test for a real bug found in live testing: following a
    # player standing indoors under a roof made the bot teleport up to the
    # roof instead of matching the player's actual (lower, indoor) Y --
    # because MOTION_BLOCKING's heightmap gives the highest solid block in
    # the whole column (the roof), not "the floor under whatever's above
    # it". The target's own tracked Y has no such ambiguity and must win.
    from minebot.protocol.chunks import ChunkHeightmap, HEIGHTMAP_TYPE_MOTION_BLOCKING

    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 5.0, 5.0, 0.0)  # target indoors at Y=5
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    heightmaps = ChunkHeightmapCache(min_y=-64)
    # Heightmap says the roof (highest solid block in the column) is way
    # up at raw height 165 (world Y=100) -- much higher than the target.
    heightmaps.handle_chunk(
        ChunkHeightmap(chunk_x=0, chunk_z=0, heights_by_type={HEIGHTMAP_TYPE_MOTION_BLOCKING: [165] * 256})
    )

    movement = MovementController(tracker, ChunkBlockCache())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=4.5, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    await asyncio.sleep(0.3)
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    # Should track toward the target's actual Y (5), nowhere near the
    # heightmap's roof height (100).
    assert result["y"] < 10.0


@pytest.mark.asyncio
async def test_follow_adjusts_y_even_when_already_within_stop_distance():
    # Regression test: standing right next to the target (e.g. they're on
    # a ledge just above/below us) must still adjust Y -- the horizontal
    # "close enough, stop moving" check must not also gate the Y-tracking,
    # or we'd never notice a pure vertical difference once already close.
    # Uses a real one-block step (climbable via step-height, no jump arc
    # needed) so real physics has solid ground to act on.
    ground = flat_ground(-10, 10, -10, 10, ground_y=0)
    ground[(0, 0)] = 1  # target's column is one block higher
    blocks = build_world(ground)

    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    # Right next to us horizontally (well within FOLLOW_STOP_DISTANCE), but
    # one block higher.
    tracker.handle_add_entity(1, target_uuid, 0.5, 2.0, 0.5)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, blocks)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.5, y=1.0, z=-1.5, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    for _ in range(20):
        await asyncio.sleep(FOLLOW_STEP_INTERVAL_SECONDS)
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    assert result["y"] > 1.0  # moved up toward the target, not stuck at the start


@pytest.mark.asyncio
async def test_mark_position_stale_clears_has_position_and_stops_follow():
    # Regression test for the "invisible after respawn" bug found in live
    # testing: our tracked position must not be trusted after a death, and
    # any in-progress follow must stop instead of chasing based on
    # wherever we died rather than the fresh spawn point.
    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 10.0, 64.0, 0.0)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, ChunkBlockCache())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=64.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    assert movement.has_position is True

    conn = RecordingConnection()
    await movement.follow(conn, None, "Alex")
    assert movement._follow_task is not None

    movement.mark_position_stale()

    assert movement.has_position is False
    assert movement._follow_task is None

    with pytest.raises(RuntimeError):
        await movement.forward(conn, None)


@pytest.mark.asyncio
async def test_follow_uses_pathfinding_to_step_down_a_staircase_instead_of_falling_through_ground():
    # Regression test for the exact bug diagnosed via live testing (see
    # FINDINGS.md): when block data is available, the bot must step down a
    # staircase toward a target on lower ground rather than trying to fall
    # straight through solid ground still beneath its own (older) column.
    ground = flat_ground(-10, 10, -10, 10, ground_y=3)
    ground[(1, 0)] = 2
    ground[(2, 0)] = 1
    ground[(3, 0)] = 0
    blocks = build_world(ground)

    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 3.5, 1.0, 0.0)  # target down at the bottom step
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, blocks)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.5, y=4.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    for _ in range(40):
        await asyncio.sleep(FOLLOW_STEP_INTERVAL_SECONDS)
        if movement.y <= 1.5:
            break
    movement.stop_follow()

    assert len(conn.sent) > 0
    # The key assertion: our reported Y actually descended toward the
    # target's, in a real path down the stairs -- not stuck floating above
    # solid ground it thinks it needs to fall through.
    assert movement.y < 3.5


@pytest.mark.asyncio
async def test_follow_falls_back_to_raw_target_position_when_blocks_unknown():
    # No chunk data at all (the default, pre-pathfinding behavior): follow
    # must still work by tracking the target's raw position directly,
    # exactly as it did before pathfinding existed.
    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 10.0, 0.0, 0.0)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, ChunkBlockCache())
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    await asyncio.sleep(0.3)
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    assert result["x"] > 0.0  # moved toward the target, not frozen in place


@pytest.mark.asyncio
async def test_follow_never_reports_a_position_below_the_real_floor():
    # Regression test for the real bug found via live testing: earlier
    # naive movement execution (straight-line x/z interpolation blended
    # with an independent gravity-ramped y) could report a position that
    # was horizontally still over solid ground while claiming a y below
    # that ground's surface -- clipping through geometry that was never
    # actually vacated. Real per-tick physics (minebot/physics/simulate.py)
    # resolves collision every tick, so this must never happen: every move
    # packet's y must be at or above the real floor height for wherever
    # (x, z) claims to be.
    ground = flat_ground(-10, 10, -10, 10, ground_y=3)
    ground[(2, 2)] = 2  # a single step down, diagonally adjacent
    blocks = build_world(ground)

    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    tracker.handle_add_entity(1, target_uuid, 2.5, 3.0, 2.5)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker, blocks)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.5, y=4.0, z=0.5, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    for _ in range(30):
        await asyncio.sleep(FOLLOW_STEP_INTERVAL_SECONDS)
    movement.stop_follow()

    assert len(conn.sent) > 0

    move_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_MOVE_PLAYER_POS_ROT")
    for packet_id, data in conn.sent:
        if packet_id != move_id:
            continue
        reader = ByteReader(data)
        x, y, z = reader.read_f64(), reader.read_f64(), reader.read_f64()
        floor_y = 3 if (math.floor(x), math.floor(z)) != (2, 2) else 2
        assert y >= floor_y - 1e-6, f"reported y={y} below the real floor ({floor_y}) at ({x}, {z})"
