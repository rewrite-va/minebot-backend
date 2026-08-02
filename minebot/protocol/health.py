"""Health/death/respawn handling.

Found the hard way in live testing: a baby zombie killed the bot and it
never recovered -- our player entity died server-side and just sat there,
since we never requested a respawn. Vanilla clients show a death screen and
either wait for the user to click "Respawn", or (if
`shouldShowDeathScreen()` is false) respawn immediately; we have no UI, so
we always respawn immediately, matching the no-death-screen behavior.

Field layouts (from the decompiled source, see FINDINGS.md):
  ClientboundLogin (PLAY-phase spawn packet, distinct from the LOGIN-state
      CLIENTBOUND_LOGIN_FINISHED): playerId: i32 is the first field -- our
      own entity id, needed to tell "it's us who died" apart from any other
      player's death broadcast in view.
  ClientboundSetHealth: health: f32, food: varint, saturation: f32.
  ClientboundPlayerCombatKill: playerId: varint, message: Component (NBT;
      not parsed here -- we don't need the death message text). The real
      client's handlePlayerCombatKill only reacts if
      `packet.playerId() == our own entity id` (see
      ClientPacketListener.handlePlayerCombatKill in the decompiled
      source) -- this packet fires for any player's death broadcast
      visible to us, not just our own.
  ServerboundClientCommand: action: varint enum ordinal
      (PERFORM_RESPAWN=0, REQUEST_STATS=1, REQUEST_GAMERULE_VALUES=2).
  ClientboundRespawn: not parsed -- carries a fresh CommonPlayerSpawnInfo,
      but no position (that arrives separately via the
      ClientboundPlayerPositionPacket we already handle in play_loop.py);
      we only need to know one arrived, not its contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from minebot.net.connection import Connection, STATE_PLAY
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.registry import REGISTRY

STATE = STATE_PLAY


class ClientCommandAction(IntEnum):
    PERFORM_RESPAWN = 0
    REQUEST_STATS = 1
    REQUEST_GAMERULE_VALUES = 2


@dataclass
class OwnEntityId:
    entity_id: int


@dataclass
class SetHealth:
    health: float
    food: int
    saturation: float


@dataclass
class PlayerCombatKill:
    player_id: int


@dataclass
class Respawned:
    pass


def parse_own_entity_id(packet_id: int, data: bytes) -> OwnEntityId | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_LOGIN":
        return None
    return OwnEntityId(entity_id=ByteReader(data).read_i32())


def parse_set_health(packet_id: int, data: bytes) -> SetHealth | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_SET_HEALTH":
        return None
    reader = ByteReader(data)
    health = reader.read_f32()
    food = reader.read_varint()
    saturation = reader.read_f32()
    return SetHealth(health=health, food=food, saturation=saturation)


def parse_player_combat_kill(packet_id: int, data: bytes) -> PlayerCombatKill | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_PLAYER_COMBAT_KILL":
        return None
    player_id = ByteReader(data).read_varint()
    return PlayerCombatKill(player_id=player_id)


def parse_respawn(packet_id: int, data: bytes) -> Respawned | None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name != "CLIENTBOUND_RESPAWN":
        return None
    return Respawned()


async def send_perform_respawn(conn: Connection) -> None:
    writer = ByteWriter()
    writer.write_varint(int(ClientCommandAction.PERFORM_RESPAWN))
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_CLIENT_COMMAND")
    await conn.send_packet(packet_id, writer.getvalue())
