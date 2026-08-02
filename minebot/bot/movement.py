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
"""

from __future__ import annotations

import asyncio
import math
import uuid as uuid_module

from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection
from minebot.protocol.entities import EntityTracker
from minebot.protocol.movement import PlayerPositionSync, REL_X, REL_X_ROT, REL_Y, REL_Y_ROT, REL_Z, send_move_player_pos_rot

FOLLOW_STEP_INTERVAL_SECONDS = 0.15
FOLLOW_STOP_DISTANCE = 2.0
# Vanilla walk speed is ~4.317 blocks/sec; matching that at our tick rate
# (rather than a fixed 1.0 block/tick, which at the old 500ms tick was
# already ~2 blocks/sec and would be far too fast at this faster tick)
# keeps movement looking like walking instead of teleport-stepping.
_WALK_SPEED_BLOCKS_PER_SECOND = 4.317
FOLLOW_STEP_DISTANCE = _WALK_SPEED_BLOCKS_PER_SECOND * FOLLOW_STEP_INTERVAL_SECONDS


class MovementController:
    def __init__(self, tracker: EntityTracker) -> None:
        self.tracker = tracker
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.yaw = 0.0
        self.pitch = 0.0
        self.has_position = False
        self._follow_task: asyncio.Task | None = None

    def sync_from_position_packet(self, sync: PlayerPositionSync) -> None:
        self.x = sync.x if not (sync.relatives & REL_X) else self.x + sync.x
        self.y = sync.y if not (sync.relatives & REL_Y) else self.y + sync.y
        self.z = sync.z if not (sync.relatives & REL_Z) else self.z + sync.z
        self.yaw = sync.yaw if not (sync.relatives & REL_Y_ROT) else self.yaw + sync.yaw
        self.pitch = sync.pitch if not (sync.relatives & REL_X_ROT) else self.pitch + sync.pitch
        self.has_position = True

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
        self.stop_follow()
        self._follow_task = asyncio.create_task(self._follow_loop(conn, target_uuid))

    def stop_follow(self) -> None:
        if self._follow_task is not None:
            self._follow_task.cancel()
            self._follow_task = None

    async def stop(self, conn: Connection, sender: uuid_module.UUID | None) -> None:
        self.stop_follow()

    async def _follow_loop(self, conn: Connection, target_uuid: uuid_module.UUID) -> None:
        while True:
            await asyncio.sleep(FOLLOW_STEP_INTERVAL_SECONDS)

            if not self.has_position:
                continue

            target = self.tracker.find_by_uuid(target_uuid)
            if target is None:
                continue

            delta_x = target.x - self.x
            delta_z = target.z - self.z
            distance = math.hypot(delta_x, delta_z)
            if distance <= FOLLOW_STOP_DISTANCE:
                continue

            step = min(FOLLOW_STEP_DISTANCE, distance - FOLLOW_STOP_DISTANCE)
            direction_x = delta_x / distance
            direction_z = delta_z / distance

            self.x += direction_x * step
            self.z += direction_z * step
            self.yaw = math.degrees(math.atan2(-direction_x, direction_z))
            await send_move_player_pos_rot(conn, self.x, self.y, self.z, self.yaw, self.pitch)


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("forward", movement.forward)
    registry.register("backward", movement.backward)
    registry.register("left", movement.strafe_left)
    registry.register("right", movement.strafe_right)
    registry.register("follow", movement.follow)
    registry.register("stop", movement.stop)
