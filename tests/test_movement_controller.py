import asyncio
import math
import uuid

import pytest

from minebot.bot.movement import MovementController
from minebot.net.connection import Connection
from minebot.net.types import ByteReader
from minebot.protocol.chunks import ChunkHeightmapCache
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

    movement = MovementController(tracker)
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

    movement = MovementController(tracker)
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
    tracker = EntityTracker()
    target_uuid = uuid.uuid4()
    # Right next to us horizontally (well within FOLLOW_STOP_DISTANCE), but
    # 3 blocks higher.
    tracker.handle_add_entity(1, target_uuid, 0.5, 3.0, 0.0)
    tracker.handle_player_info([PlayerListEntry(profile_id=target_uuid, name="Alex")])

    movement = MovementController(tracker)
    movement.sync_from_position_packet(
        PlayerPositionSync(teleport_id=1, x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, relatives=0)
    )
    conn = RecordingConnection()

    await movement.follow(conn, None, "Alex")
    await asyncio.sleep(0.3)
    movement.stop_follow()

    assert len(conn.sent) > 0
    result = _last_move(conn)
    assert result["y"] > 0.0  # moved up toward the target, not stuck at 0


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

    movement = MovementController(tracker)
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


def test_step_toward_target_height_climbing_is_capped_at_step_height():
    from minebot.bot.movement import FOLLOW_MAX_UPWARD_STEP

    movement = MovementController(EntityTracker())
    movement.y = 0.0

    # Target far above -- should rise by at most one step-height per call,
    # not jump straight there (a normal walking step-up, not a teleport).
    movement._step_toward_target_height(10.0)
    assert movement.y == pytest.approx(FOLLOW_MAX_UPWARD_STEP)
    assert movement._vertical_velocity == 0.0


def test_step_toward_target_height_climbing_within_step_height_snaps_exactly():
    movement = MovementController(EntityTracker())
    movement.y = 0.0

    movement._step_toward_target_height(0.3)  # less than the 0.6 step cap
    assert movement.y == pytest.approx(0.3)


def test_step_toward_target_height_falling_accelerates():
    # Regression test for the real bug found live: a flat-rate ramp toward
    # a lower Y (no accumulated velocity) reads to the server as an
    # implausible jump and gets corrected back every tick. Falling must
    # instead build up speed like a real client's own gravity simulation
    # (LivingEntity.DEFAULT_BASE_GRAVITY = 0.08 blocks/tick^2), so
    # consecutive calls should fall progressively *farther* per call, not a
    # constant amount.
    movement = MovementController(EntityTracker())
    movement.y = 100.0

    movement._step_toward_target_height(-1000.0)  # far below -- stay falling
    first_drop = 100.0 - movement.y

    movement._step_toward_target_height(-1000.0)
    second_drop = (100.0 - first_drop) - movement.y

    assert first_drop > 0.0
    assert second_drop > first_drop  # accelerating, not constant speed
    assert movement._vertical_velocity < 0.0  # still falling


def test_step_toward_target_height_falling_approaches_terminal_velocity():
    # Cross-checked against mineflayer's own physics engine
    # (prismarine-physics): air drag (1 - 0.02 per tick) is applied to
    # vertical velocity after gravity every tick, giving a bounded terminal
    # fall speed (gravity / (1 - airdrag) = 0.08 / 0.02 = 4.0 blocks/tick at
    # the limit) rather than unbounded linear acceleration -- matters on
    # long falls, where a naive "just keep subtracting gravity" model would
    # eventually report implausibly fast speeds.
    movement = MovementController(EntityTracker())
    movement.y = 10_000_000.0  # far enough to fall for a long time uninterrupted

    for _ in range(2000):
        movement._step_toward_target_height(-100_000_000.0)

    # Should be close to the theoretical terminal velocity, and in
    # particular nowhere near what unbounded linear acceleration would give
    # (0.08 * 2000 * ticks-per-step, orders of magnitude larger).
    assert movement._vertical_velocity == pytest.approx(-4.0, abs=0.1)


def test_step_toward_target_height_falling_lands_exactly_on_target():
    movement = MovementController(EntityTracker())
    movement.y = 10.0

    # Small drop, well within what even the first tick of falling covers --
    # should land exactly on the target and reset velocity, not overshoot.
    movement._step_toward_target_height(9.99)
    assert movement.y == pytest.approx(9.99)
    assert movement._vertical_velocity == 0.0


def test_step_toward_target_height_switching_from_fall_to_climb_resets_velocity():
    movement = MovementController(EntityTracker())
    movement.y = 100.0

    movement._step_toward_target_height(-1000.0)
    assert movement._vertical_velocity < 0.0

    # Target is now above us (e.g. we landed and the target went back up
    # stairs) -- climbing must not carry over leftover fall velocity.
    movement._step_toward_target_height(movement.y + 5.0)
    assert movement._vertical_velocity == 0.0
