"""Movement command handlers (MVP scope from prompt.txt: forward, backward,
left, right) plus !follow/!stop.

Position/rotation tracking: MovementController.x/y/z/yaw/pitch are kept in
sync from ClientboundPlayerPositionPacket (see bot/play_loop.py, which calls
sync_from_position_packet on every one received and replies with
ServerboundAcceptTeleportationPacket -- required, or the server disconnects
us for not acknowledging the teleport). All movement sends are relative to
this tracked state, since the server only trusts positions in the
neighborhood of where it last placed us.

Direction convention matches vanilla: yaw 0 faces +Z (south), increasing
clockwise when viewed from above; "forward" moves along -sin(yaw), cos(yaw)
in the (x, z) plane at the current yaw -- standard Minecraft yaw-to-direction
math, unchanged across versions.

Every handler takes `sender` as its second argument (after `conn`), matching
run_play_loop's dispatch(text, conn, sender) contract -- `sender` is the
speaking player's UUID for player chat, or None for system chat/console.
!follow uses it as the default target so "!follow" with no argument follows
whoever typed it; an explicit "!follow(\"name\")" still works by resolving
the name through the entity tracker's tab-list mapping instead.

Y-axis tracking while following: follows the **target's own tracked Y**
(from EntityTracker, fed by AddEntity/TeleportEntity/EntityPositionSync/
MoveEntity -- see entities.py), not a heightmap lookup. This matters
indoors: a column-wide "highest solid block" heightmap
(minebot/protocol/chunks.py's ChunkHeightmapCache) can't tell "the floor
under a roof" from "the roof itself" -- if the target is standing under a
ceiling, MOTION_BLOCKING's topmost hit in that column is the ceiling, not
the floor. The target's own reported Y has no such ambiguity, since it's
wherever the server actually says they're standing (this is a real bug we
hit in live testing: the bot initially teleported up to the roof instead
of matching the target's indoor Y).

Falling physics (found necessary via live testing -- see FINDINGS.md
"Why the bot got stuck on stairs"): movement authority in vanilla is
client-side -- the real client simulates its own gravity/collision each
tick and reports the already-resolved position; the server
(ServerGamePacketListenerImpl.handleMovePlayer, read from the decompiled
source) only rejects a reported position when it deviates too far from
*its own tracked expectation* of the player's velocity, which itself is
built up from the player's own prior reported deltas. Jumping straight
toward a lower target Y (no accumulated falling velocity) reads to the
server as "moved too quickly" and gets silently corrected back every
tick -- which is exactly what live testing showed (the bot never actually
descended a staircase; the server kept resetting it). Fixed by tracking a
real vertical velocity that accelerates under vanilla's actual gravity
constant (LivingEntity.DEFAULT_BASE_GRAVITY = 0.08 blocks per 20Hz game
tick, i.e. -0.08 blocks/tick^2 added to velocity every tick) while falling,
so our reported per-tick position deltas look like genuine falling motion
to the server instead of an arbitrary jump. Climbing upward by a small
amount (e.g. a single stair step) doesn't need this: vanilla's own player
step-up height (LivingEntity.maxUpStep / Attributes.STEP_HEIGHT) is 0.6
blocks, handled as ordinary walking collision response, not gravity/jump
physics -- so small upward Y changes are still just clamped to a flat
per-tick cap, only the downward (falling) case needs real acceleration.
This is still not full physics: no actual jump impulse (we can't gain
upward velocity the way pressing space does), and the server may still
reject movement in ways we haven't seen yet.

Path planning (minebot/pathfinding/ -- a port of mineflayer-pathfinder's
astar.js/movements.js/goals.js, walk+climb+parkour moves only, no
dig/place): when the target's chunk data is loaded, the follow loop
computes an A* path toward a GoalNear around the target and steers toward
the path's next waypoint instead of the target's raw (x, y, z) directly.
This is what actually fixes the reported "bot tries to fall through solid
ground when behind a target who drops a level" bug -- raw-Y-following has
no idea whether the space between the bot and the target's new Y is open,
while the pathfinder does. Falls back to the previous raw-target-following
behavior when we don't have block data for the relevant area yet (e.g. we
haven't received those chunks) or when no path is found, rather than
freezing -- following imperfectly is better than not moving at all.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid as uuid_module

from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection
from minebot.pathfinding.astar import AStar
from minebot.pathfinding.goals import GoalNear
from minebot.pathfinding.move import Move as PathMove
from minebot.pathfinding.movements import Movements
from minebot.protocol.chunk_blocks import ChunkBlockCache
from minebot.protocol.entities import EntityTracker
from minebot.protocol.movement import PlayerPositionSync, REL_X, REL_X_ROT, REL_Y, REL_Y_ROT, REL_Z, send_move_player_pos_rot

log = logging.getLogger("minebot.movement")

FOLLOW_STEP_INTERVAL_SECONDS = 0.15
FOLLOW_STOP_DISTANCE = 2.0
# Vanilla walk speed is ~4.317 blocks/sec; matching that at our tick rate
# (rather than a fixed 1.0 block/tick, which at the old 500ms tick was
# already ~2 blocks/sec and would be far too fast at this faster tick)
# keeps movement looking like walking instead of teleport-stepping.
_WALK_SPEED_BLOCKS_PER_SECOND = 4.317
FOLLOW_STEP_DISTANCE = _WALK_SPEED_BLOCKS_PER_SECOND * FOLLOW_STEP_INTERVAL_SECONDS

# Vanilla's real per-game-tick gravity constant (LivingEntity.DEFAULT_BASE_GRAVITY,
# confirmed in the decompiled source, and independently cross-checked
# against mineflayer's own physics engine, prismarine-physics -- both give
# exactly 0.08), applied at the real 20Hz game tick rate (50ms/tick)
# regardless of our own slower follow-loop tick rate. AIR_DRAG (also
# cross-checked against prismarine-physics: `airdrag: 1 - 0.02`) is
# applied multiplicatively to vertical velocity every tick *after* gravity,
# giving falling a terminal velocity instead of unbounded linear
# acceleration -- matters for longer drops, not just short stair-height ones.
_GRAVITY_PER_GAME_TICK = 0.08
_AIR_DRAG_PER_GAME_TICK = 1.0 - 0.02
_GAME_TICK_SECONDS = 0.05
_GAME_TICKS_PER_FOLLOW_STEP = FOLLOW_STEP_INTERVAL_SECONDS / _GAME_TICK_SECONDS

# Vanilla's player step-up height (LivingEntity.maxUpStep / Attributes.STEP_HEIGHT):
# climbing up to this much in one step is just normal walking collision
# response, not jump physics -- used as the flat per-tick cap for the
# upward case, which doesn't need gravity simulation.
FOLLOW_MAX_UPWARD_STEP = 0.6

# How long A* is allowed to spend per invocation -- generous relative to
# the follow loop's own tick rate (FOLLOW_STEP_INTERVAL_SECONDS) since a
# search only needs to run once every FOLLOW_REPLAN_MIN_DISTANCE, not
# every tick.
PATHFINDING_TIMEOUT_SECONDS = 1.0
# Re-run A* only once the target has moved this far from where the last
# computed path was aimed -- matches GoalFollow.has_changed()'s role in
# mineflayer-pathfinder: avoids recomputing a path every single tick for a
# target that's barely moved.
FOLLOW_REPLAN_DISTANCE = 2.0


class MovementController:
    def __init__(self, tracker: EntityTracker, blocks: ChunkBlockCache) -> None:
        self.tracker = tracker
        self.blocks = blocks
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.yaw = 0.0
        self.pitch = 0.0
        self.has_position = False
        self._follow_task: asyncio.Task | None = None
        # Accumulated downward speed while following into a fall (blocks
        # per game tick, negative while falling); reset to 0 whenever we're
        # not currently falling. See module docstring for why this needs to
        # exist at all -- the server rejects a sudden Y jump that isn't
        # backed by an accelerating velocity like a real client would report.
        self._vertical_velocity = 0.0
        # Current planned path (list of pathfinding.move.Move waypoints,
        # nearest-first) and the target position it was computed for, so we
        # know when it's gone stale enough to recompute (see
        # FOLLOW_REPLAN_DISTANCE) rather than re-running A* every tick.
        self._current_path: list[PathMove] = []
        self._path_computed_for: tuple[float, float, float] | None = None

    def sync_from_position_packet(self, sync: PlayerPositionSync) -> None:
        self.x = sync.x if not (sync.relatives & REL_X) else self.x + sync.x
        self.y = sync.y if not (sync.relatives & REL_Y) else self.y + sync.y
        self.z = sync.z if not (sync.relatives & REL_Z) else self.z + sync.z
        self.yaw = sync.yaw if not (sync.relatives & REL_Y_ROT) else self.yaw + sync.yaw
        self.pitch = sync.pitch if not (sync.relatives & REL_X_ROT) else self.pitch + sync.pitch
        self.has_position = True

    def mark_position_stale(self) -> None:
        """Call on respawn: our tracked x/y/z is from wherever we died, not
        the new spawn point, and movement/follow must not act on it until a
        fresh ClientboundPlayerPositionPacket arrives. Also cancels any
        active follow -- continuing to chase someone while dead/respawning
        would just spam movement packets against stale state.
        """
        self.has_position = False
        self._vertical_velocity = 0.0
        self._current_path = []
        self._path_computed_for = None
        self.stop_follow()

    async def _walk_by(self, conn: Connection, dx: float, dz: float) -> None:
        if not self.has_position:
            raise RuntimeError("don't know our own position yet (no ClientboundPlayerPosition received)")
        self.x += dx
        self.z += dz
        await send_move_player_pos_rot(conn, self.x, self.y, self.z, self.yaw, self.pitch)

    async def forward(self, conn: Connection, sender: uuid_module.UUID | None, distance: float = 1.0) -> None:
        yaw_rad = math.radians(self.yaw)
        await self._walk_by(conn, -math.sin(yaw_rad) * distance, math.cos(yaw_rad) * distance)

    async def backward(self, conn: Connection, sender: uuid_module.UUID | None, distance: float = 1.0) -> None:
        yaw_rad = math.radians(self.yaw)
        await self._walk_by(conn, math.sin(yaw_rad) * distance, -math.cos(yaw_rad) * distance)

    async def strafe_left(self, conn: Connection, sender: uuid_module.UUID | None, distance: float = 1.0) -> None:
        yaw_rad = math.radians(self.yaw)
        await self._walk_by(conn, -math.cos(yaw_rad) * distance, -math.sin(yaw_rad) * distance)

    async def strafe_right(self, conn: Connection, sender: uuid_module.UUID | None, distance: float = 1.0) -> None:
        yaw_rad = math.radians(self.yaw)
        await self._walk_by(conn, math.cos(yaw_rad) * distance, math.sin(yaw_rad) * distance)

    async def follow(self, conn: Connection, sender: uuid_module.UUID | None, player_name: str | None = None) -> None:
        target_uuid = self.tracker.name_to_uuid.get(player_name) if player_name else sender
        if target_uuid is None:
            raise RuntimeError(
                "no player name given and no sender uuid available to follow "
                "(e.g. triggered from console/system chat, not a player message)"
            )
        log.info("starting follow of %s", target_uuid)
        self.stop_follow()
        self._current_path = []
        self._path_computed_for = None
        self._follow_task = asyncio.create_task(self._follow_loop(conn, target_uuid))

    def stop_follow(self) -> None:
        if self._follow_task is not None:
            self._follow_task.cancel()
            self._follow_task = None

    async def stop(self, conn: Connection, sender: uuid_module.UUID | None) -> None:
        self.stop_follow()

    def _maybe_replan_path(self, target_x: float, target_y: float, target_z: float) -> None:
        """(Re)computes an A* path toward the target if we don't have a
        current one, or the target has moved far enough that the existing
        one is stale (mirrors mineflayer-pathfinder's GoalFollow.hasChanged,
        which exists for the same reason: replanning every single tick for
        a target that's barely moved is wasted work).

        Leaves self._current_path empty (not an exception) when we don't
        have block data for the relevant area yet or no path is found --
        the follow loop falls back to raw target-following in that case,
        same as before pathfinding existed, rather than freezing.
        """
        if self._path_computed_for is not None:
            last_x, last_y, last_z = self._path_computed_for
            moved = math.dist((last_x, last_y, last_z), (target_x, target_y, target_z))
            if moved <= FOLLOW_REPLAN_DISTANCE and self._current_path:
                return  # existing path is still aimed close enough to the target

        self._path_computed_for = (target_x, target_y, target_z)

        start_x, start_y, start_z = math.floor(self.x), math.floor(self.y), math.floor(self.z)
        if self.blocks.block_state_at(start_x, start_z, start_y - 1) is None:
            # We don't have block data under our own feet -- e.g. chunks
            # haven't loaded yet. Pathfinding can't do better than guessing
            # here, so don't pretend to have a plan.
            self._current_path = []
            return

        movements = Movements(blocks=self.blocks)
        start = PathMove(start_x, start_y, start_z, 0.0)
        goal = GoalNear(target_x, target_y, target_z, range=FOLLOW_STOP_DISTANCE)
        astar = AStar(start, movements.get_neighbors, goal.heuristic, goal.is_end, timeout=PATHFINDING_TIMEOUT_SECONDS)
        result = astar.compute()

        if result.status in ("success", "partial") and result.path:
            self._current_path = result.path
            log.debug("follow: planned path of %d waypoints (status=%s)", len(result.path), result.status)
        else:
            self._current_path = []
            log.debug("follow: no path found (status=%s), falling back to raw target-following", result.status)

    def _next_waypoint(self) -> PathMove | None:
        """Pops and returns waypoints we've already reached, then returns
        the next one still ahead of us -- or None once the path is
        exhausted (the caller falls back to the target's raw position,
        which by then should be within FOLLOW_STOP_DISTANCE anyway since
        the path was planned toward a GoalNear around it).
        """
        while self._current_path:
            waypoint = self._current_path[0]
            reached = (
                math.floor(self.x) == waypoint.x
                and math.floor(self.z) == waypoint.z
                and abs(self.y - waypoint.y) < 1.0
            )
            if not reached:
                return waypoint
            self._current_path.pop(0)
        return None

    async def _follow_loop(self, conn: Connection, target_uuid: uuid_module.UUID) -> None:
        warned_no_position = False
        warned_no_target = False

        while True:
            await asyncio.sleep(FOLLOW_STEP_INTERVAL_SECONDS)

            if not self.has_position:
                if not warned_no_position:
                    log.warning("follow(%s) idling: we don't know our own position yet", target_uuid)
                    warned_no_position = True
                continue
            warned_no_position = False

            target = self.tracker.find_by_uuid(target_uuid)
            if target is None:
                if not warned_no_target:
                    log.warning("follow(%s) idling: target not found in entity tracker", target_uuid)
                    warned_no_target = True
                continue
            warned_no_target = False

            self._maybe_replan_path(target.x, target.y, target.z)
            waypoint = self._next_waypoint()
            aim_x, aim_y, aim_z = (waypoint.x, waypoint.y, waypoint.z) if waypoint is not None else (target.x, target.y, target.z)

            delta_x = aim_x - self.x
            delta_z = aim_z - self.z
            distance = math.hypot(delta_x, delta_z)
            # Only the raw target position (not a waypoint) should ever
            # stop horizontal movement -- stopping short of an intermediate
            # waypoint would leave us stuck mid-path.
            stop_distance = FOLLOW_STOP_DISTANCE if waypoint is None else 0.0

            log.debug(
                "follow tick: self=(%.2f,%.2f,%.2f) aim=(%.2f,%.2f,%.2f) target=(%.2f,%.2f,%.2f) distance=%.2f",
                self.x, self.y, self.z, aim_x, aim_y, aim_z, target.x, target.y, target.z, distance,
            )

            moved_horizontally = distance > stop_distance
            if moved_horizontally:
                step = min(FOLLOW_STEP_DISTANCE, distance - stop_distance)
                direction_x = delta_x / distance
                direction_z = delta_z / distance
                self.x += direction_x * step
                self.z += direction_z * step
                self.yaw = math.degrees(math.atan2(-direction_x, direction_z))

            # Adjust Y even when standing right next to the aim point (e.g.
            # they're on a ledge just above/below us) -- gating this behind
            # moved_horizontally would mean never noticing a Y difference
            # once we're within stopping distance.
            moved_vertically = abs(aim_y - self.y) > 1e-6
            if not moved_horizontally and not moved_vertically:
                continue

            self._step_toward_target_height(aim_y)
            log.debug("follow sending move: (%.2f, %.2f, %.2f)", self.x, self.y, self.z)
            await send_move_player_pos_rot(conn, self.x, self.y, self.z, self.yaw, self.pitch)

    def _step_toward_target_height(self, target_y: float) -> None:
        """Moves self.y toward the target's own tracked Y (see module
        docstring for why this, not a heightmap lookup, is the primary
        signal -- a heightmap can't distinguish a floor under a roof from
        the roof itself, but the target's actual reported Y always can).

        Falling (target below us) accelerates under vanilla's real gravity
        constant across however many real 20Hz game ticks our follow-loop
        tick spans, so the server sees a plausible, accelerating fall
        instead of an arbitrary jump. Climbing (target above us) is capped
        at vanilla's player step-up height per tick instead -- ordinary
        walking collision response, not physics that needs acceleration.
        """
        delta_y = target_y - self.y

        if delta_y >= 0:
            # Climbing up: no velocity/acceleration involved in vanilla for
            # a normal step-up, just cap how much we claim to rise at once.
            self._vertical_velocity = 0.0
            if delta_y <= FOLLOW_MAX_UPWARD_STEP:
                self.y = target_y
            else:
                self.y += FOLLOW_MAX_UPWARD_STEP
            return

        # Falling: accumulate downward velocity over the real number of
        # 20Hz game ticks this follow-loop tick represents, then apply the
        # resulting displacement -- this is what makes the server's own
        # velocity tracking agree with what we're reporting. Order matches
        # prismarine-physics: subtract gravity, then apply air drag, each
        # tick (drag gives a terminal velocity instead of unbounded
        # acceleration on long falls).
        remaining_ticks = _GAME_TICKS_PER_FOLLOW_STEP
        while remaining_ticks > 0:
            tick_fraction = min(1.0, remaining_ticks)
            self._vertical_velocity -= _GRAVITY_PER_GAME_TICK * tick_fraction
            self._vertical_velocity *= _AIR_DRAG_PER_GAME_TICK ** tick_fraction
            proposed_y = self.y + self._vertical_velocity * tick_fraction
            if proposed_y <= target_y:
                # Landed partway through this simulation step.
                self.y = target_y
                self._vertical_velocity = 0.0
                return
            self.y = proposed_y
            remaining_ticks -= tick_fraction


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("forward", movement.forward)
    registry.register("backward", movement.backward)
    registry.register("left", movement.strafe_left)
    registry.register("right", movement.strafe_right)
    registry.register("follow", movement.follow)
    registry.register("stop", movement.stop)
