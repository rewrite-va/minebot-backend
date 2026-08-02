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

Real physics simulation (minebot/physics/ -- a port of
prismarine-physics@1.5.2, the actual engine mineflayer-pathfinder relies on
for movement execution): earlier phases of this session tried
hand-rolled approximations of vanilla physics (a flat per-tick Y ramp, then
a gravity-accelerated Y ramp blended with straight-line x/z interpolation).
Both were eventually caught by live testing producing subtly-wrong
positions the server rejected -- most concretely, a diagonal move that
changes both (x, z) and y at once (stepping down a ledge) would, under
straight-line-plus-independent-Y-ramp execution, briefly claim a position
that's horizontally *inside* the block being stepped off of, before
vertical collision was actually resolved. Real Minecraft (and mineflayer)
never blends axes like that: every real client resolves horizontal and
vertical collision as separate swept-AABB checks each individual 20Hz game
tick. `minebot/physics/simulate.py` ports that real per-tick collision
resolution (gravity, jump impulse, step-height climbing, ladders) against
real per-block collision shapes from block_registry.py, so the position we
report each follow tick is the same kind of already-resolved, physically
consistent state a real client would produce -- not an approximation of one.

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

Movement execution: each follow-loop tick feeds simple look-and-walk
control inputs (forward=True, jump=True whenever the aim point is above
us) into the physics simulation for however many real 20Hz game ticks the
follow-loop's own tick interval spans, then reports the resulting
position/on_ground state -- the same pattern mineflayer-pathfinder's own
physics.js uses (its getController), just without the full sprint-jump/
parkour-timing logic mineflayer's move.js adds on top (not needed yet;
this session's pathfinding port already excludes parkour execution for
the same reason -- see pathfinding/movements.py).
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
from minebot.physics.simulate import PlayerPhysicsState, simulate_tick
from minebot.protocol.chunk_blocks import ChunkBlockCache
from minebot.protocol.entities import EntityTracker
from minebot.protocol.movement import PlayerPositionSync, REL_X, REL_X_ROT, REL_Y, REL_Y_ROT, REL_Z, send_move_player_pos_rot

log = logging.getLogger("minebot.movement")

FOLLOW_STEP_INTERVAL_SECONDS = 0.15
FOLLOW_STOP_DISTANCE = 2.0
_GAME_TICK_SECONDS = 0.05  # real vanilla tick rate (20Hz), independent of our own follow-loop tick rate
_GAME_TICKS_PER_FOLLOW_STEP = FOLLOW_STEP_INTERVAL_SECONDS / _GAME_TICK_SECONDS

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
        # Current planned path (list of pathfinding.move.Move waypoints,
        # nearest-first) and the target position it was computed for, so we
        # know when it's gone stale enough to recompute (see
        # FOLLOW_REPLAN_DISTANCE) rather than re-running A* every tick.
        self._current_path: list[PathMove] = []
        self._path_computed_for: tuple[float, float, float] | None = None
        # Real per-tick physics state (minebot/physics/simulate.py), kept
        # in sync with x/y/z/yaw on every position update. on_ground and
        # vel_x/y/z persist across follow ticks -- they're exactly the
        # "accumulated real motion" state a real client would have, which
        # is what makes our reported positions look physically consistent
        # to the server instead of an arbitrary jump each tick.
        self._physics = PlayerPhysicsState(x=0.0, y=0.0, z=0.0, yaw=0.0)

    def sync_from_position_packet(self, sync: PlayerPositionSync) -> None:
        self.x = sync.x if not (sync.relatives & REL_X) else self.x + sync.x
        self.y = sync.y if not (sync.relatives & REL_Y) else self.y + sync.y
        self.z = sync.z if not (sync.relatives & REL_Z) else self.z + sync.z
        self.yaw = sync.yaw if not (sync.relatives & REL_Y_ROT) else self.yaw + sync.yaw
        self.pitch = sync.pitch if not (sync.relatives & REL_X_ROT) else self.pitch + sync.pitch
        self.has_position = True
        # A server-authoritative sync always wins over whatever the
        # physics simulation had accumulated -- resync position and reset
        # velocity/on_ground rather than carrying over simulated motion
        # from before a teleport/join, which wouldn't reflect anything the
        # server actually agreed to.
        self._physics.x, self._physics.y, self._physics.z = self.x, self.y, self.z
        self._physics.yaw = math.pi - math.radians(self.yaw)
        self._physics.vel_x = self._physics.vel_y = self._physics.vel_z = 0.0
        self._physics.on_ground = False

    def mark_position_stale(self) -> None:
        """Call on respawn: our tracked x/y/z is from wherever we died, not
        the new spawn point, and movement/follow must not act on it until a
        fresh ClientboundPlayerPositionPacket arrives. Also cancels any
        active follow -- continuing to chase someone while dead/respawning
        would just spam movement packets against stale state.
        """
        self.has_position = False
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
            if waypoint is not None:
                aim_x, aim_y, aim_z = waypoint.x + 0.5, waypoint.y, waypoint.z + 0.5
            else:
                aim_x, aim_y, aim_z = target.x, target.y, target.z

            distance = math.hypot(aim_x - self.x, aim_z - self.z)
            stop_distance = FOLLOW_STOP_DISTANCE if waypoint is None else 0.0
            log.debug(
                "follow tick: self=(%.2f,%.2f,%.2f) aim=(%.2f,%.2f,%.2f) target=(%.2f,%.2f,%.2f) distance=%.2f",
                self.x, self.y, self.z, aim_x, aim_y, aim_z, target.x, target.y, target.z, distance,
            )
            if distance <= stop_distance and abs(aim_y - self.y) < 0.1:
                continue  # already there -- nothing to simulate this tick

            self._simulate_toward(aim_x, aim_y, aim_z)
            log.debug(
                "follow sending move: (%.2f, %.2f, %.2f) on_ground=%s",
                self.x, self.y, self.z, self._physics.on_ground,
            )
            await send_move_player_pos_rot(
                conn, self.x, self.y, self.z, self.yaw, self.pitch, on_ground=self._physics.on_ground,
            )

    def _simulate_toward(self, aim_x: float, aim_y: float, aim_z: float) -> None:
        """Feeds simple look-and-walk control inputs into the real physics
        simulation (minebot/physics/simulate.py) for however many real
        20Hz game ticks this follow-loop tick spans, then updates
        x/y/z/yaw from the resulting state -- mirroring
        mineflayer-pathfinder's own physics.js getController (aim yaw at
        the target, hold forward, jump when useful) rather than computing
        a position directly the way earlier phases of this session tried.

        Jump is held whenever the aim point is above us: real vanilla
        jumping is a one-tick velocity impulse resolved by ordinary
        gravity afterward, not something we compute the arc for -- holding
        the control matches how a real player reaches a ledge one step
        higher than automatic step-height climbing covers.
        """
        self._physics.x, self._physics.y, self._physics.z = self.x, self.y, self.z
        self._physics.control.jump = aim_y > self._physics.y + 0.1

        # physics/simulate.py's _apply_heading computes its own internal
        # facing as (pi - state.yaw) -- mirroring prismarine-physics's own
        # `yaw = Math.PI - entity.yaw` exactly -- so its "forward" unit
        # vector is (-sin(pi-state.yaw), cos(pi-state.yaw)), which only
        # equals the direction we actually want to walk in
        # (direction_x, direction_z, normalized) when state.yaw is set to
        # atan2(-direction_x, -direction_z), not the more intuitive-looking
        # atan2(direction_x, direction_z) -- verified numerically, not
        # assumed, since getting this backwards silently makes the bot walk
        # directly away from wherever it's aiming.
        remaining_ticks = _GAME_TICKS_PER_FOLLOW_STEP
        while remaining_ticks >= 1.0:
            direction_x = aim_x - self._physics.x
            direction_z = aim_z - self._physics.z
            direction_distance = math.hypot(direction_x, direction_z)
            if direction_distance > 0.05:
                self._physics.yaw = math.atan2(-direction_x, -direction_z)
                self._physics.control.forward = True
            else:
                self._physics.control.forward = False

            simulate_tick(self._physics, self.blocks)
            remaining_ticks -= 1.0

        self.x, self.y, self.z = self._physics.x, self._physics.y, self._physics.z
        # Undo the module docstring's forward-direction reconciliation
        # (state.yaw = pi - radians(self.yaw)) to get back our own
        # degrees-based convention for the outgoing move packet.
        self.yaw = math.degrees(math.pi - self._physics.yaw)


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("forward", movement.forward)
    registry.register("backward", movement.backward)
    registry.register("left", movement.strafe_left)
    registry.register("right", movement.strafe_right)
    registry.register("follow", movement.follow)
    registry.register("stop", movement.stop)
