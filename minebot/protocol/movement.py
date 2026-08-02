"""PLAY-phase movement: our own position (spawn/teleport sync) and sending
walk/turn commands via ServerboundMovePlayerPacket.

Field layouts from the decompiled source (see FINDINGS.md):
  ClientboundPlayerPositionPacket(id: varint, change: PositionMoveRotation,
      relatives: Set<Relative>)
  PositionMoveRotation = position: Vec3(f64,f64,f64), deltaMovement: Vec3(f64,f64,f64),
      yRot: f32, xRot: f32
  Relative.SET_STREAM_CODEC: a raw (non-varint) i32 bitmask, bit N = Relative
      enum ordinal N (X=0, Y=1, Z=2, Y_ROT=3, X_ROT=4, DELTA_X=5, DELTA_Y=6,
      DELTA_Z=7, ROTATE_DELTA=8)
  ServerboundAcceptTeleportationPacket(id: varint) -- echo the same id back
  ServerboundMovePlayerPacket.PosRot(x,y,z: f64, yRot,xRot: f32, flags: u8)
      -- flags bit0=onGround, bit1=horizontalCollision

We only track absolute x/y/z/yaw/pitch for our own player, applying the
relative-flag semantics from ClientboundPlayerPositionPacket (each axis is
either absolute or an offset added to our last known position/rotation --
in practice vanilla servers send an all-absolute sync on spawn/teleport, so
relative offsets are handled but not expected to be exercised heavily).
"""

from __future__ import annotations

from dataclasses import dataclass

from minebot.net.connection import Connection, STATE_PLAY
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.registry import REGISTRY

STATE = STATE_PLAY

REL_X = 1 << 0
REL_Y = 1 << 1
REL_Z = 1 << 2
REL_Y_ROT = 1 << 3
REL_X_ROT = 1 << 4


@dataclass
class PlayerPositionSync:
    teleport_id: int
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    relatives: int  # Relative bitmask; see REL_* constants above


def parse_player_position(packet_id: int, data: bytes) -> PlayerPositionSync | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_PLAYER_POSITION":
        return None

    reader = ByteReader(data)
    teleport_id = reader.read_varint()
    pos_x = reader.read_f64()
    pos_y = reader.read_f64()
    pos_z = reader.read_f64()
    reader.read_f64()  # deltaMovement.x, unused
    reader.read_f64()  # deltaMovement.y, unused
    reader.read_f64()  # deltaMovement.z, unused
    yaw = reader.read_f32()
    pitch = reader.read_f32()
    relatives = reader.read_i32()

    return PlayerPositionSync(
        teleport_id=teleport_id,
        x=pos_x,
        y=pos_y,
        z=pos_z,
        yaw=yaw,
        pitch=pitch,
        relatives=relatives,
    )


async def send_accept_teleportation(conn: Connection, teleport_id: int) -> None:
    writer = ByteWriter()
    writer.write_varint(teleport_id)
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_ACCEPT_TELEPORTATION")
    await conn.send_packet(packet_id, writer.getvalue())


async def send_move_player_pos_rot(
    conn: Connection, x: float, y: float, z: float, yaw: float, pitch: float, on_ground: bool = True
) -> None:
    writer = ByteWriter()
    writer.write_f64(x)
    writer.write_f64(y)
    writer.write_f64(z)
    writer.write_f32(yaw)
    writer.write_f32(pitch)
    writer.write_u8(1 if on_ground else 0)
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_MOVE_PLAYER_POS_ROT")
    await conn.send_packet(packet_id, writer.getvalue())
