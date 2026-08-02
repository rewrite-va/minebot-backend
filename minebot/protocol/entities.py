"""Tracks other entities' positions (needed for !follow) and the tab-list
name<->UUID mapping (needed to resolve a player name typed in chat to an
entity to follow).

Field layouts from the decompiled source (see FINDINGS.md):
  ClientboundAddEntityPacket: id: varint, uuid: uuid, type: varint (registry
      id -- not decoded, we only care about matching by uuid), x/y/z: f64,
      movement: Vec3(f64,f64,f64), xRot/yRot/yHeadRot: i8 (packed degrees,
      not unpacked -- we don't need entity rotation), data: varint
  ClientboundRemoveEntitiesPacket: entityIds: varint-prefixed list of varints
  ClientboundTeleportEntityPacket / ClientboundEntityPositionSyncPacket:
      id: varint, change: PositionMoveRotation (position+deltaMovement
      Vec3s, yRot/xRot f32), relatives: Relative bitmask (i32), [onGround: bool]
  ClientboundMoveEntityPacket.Pos/PosRot: id: varint, xa/ya/za: i16 (relative
      position deltas in units of 1/4096 block -- the classic fixed-point
      encoding used since ~1.8), [yRot/xRot: i8, not decoded], onGround: bool

  ClientboundPlayerInfoUpdatePacket: actions: EnumSet<Action> as a
      fixed-size bitset (ceil(8 actions / 8) = 1 byte, bit N = action enum
      ordinal N), then a varint-prefixed list of entries; each entry is
      { profileId: uuid, then per-action-in-`actions`-order a field }.
      We only care about ADD_PLAYER's (name, PropertyMap) pair to get
      username<->uuid; every other action's payload still has to be parsed
      (or precisely skipped) to keep the reader position correct for
      subsequent entries, since fields are positional per active action.
"""

from __future__ import annotations

import uuid as uuid_module
from dataclasses import dataclass, field

from minebot.net.types import ByteReader
from minebot.protocol.registry import REGISTRY

STATE = "play"

# Action enum ordinals, in declaration order (see ClientboundPlayerInfoUpdatePacket.Action):
ACTION_ADD_PLAYER = 0
ACTION_INITIALIZE_CHAT = 1
ACTION_UPDATE_GAME_MODE = 2
ACTION_UPDATE_LISTED = 3
ACTION_UPDATE_LATENCY = 4
ACTION_UPDATE_DISPLAY_NAME = 5
ACTION_UPDATE_LIST_ORDER = 6
ACTION_UPDATE_HAT = 7
_ACTION_COUNT = 8


def _read_action_bitset(reader: ByteReader) -> set[int]:
    (byte,) = reader.read(1)
    return {i for i in range(_ACTION_COUNT) if byte & (1 << i)}


def _skip_game_profile_properties(reader: ByteReader) -> None:
    count = reader.read_varint()
    for _ in range(count):
        reader.read_utf()  # name
        reader.read_utf()  # value
        if reader.read_bool():
            reader.read_utf()  # signature


def _skip_nullable_public_key_data(reader: ByteReader) -> None:
    # ProfilePublicKey.Data: expiresAt: Instant (a plain i64 epoch-millis --
    # NOT i64 seconds + i32 nanos, see FriendlyByteBuf.readInstant), key:
    # length-prefixed DER bytes (readPublicKey -> readByteArray(512)),
    # keySignature: length-prefixed bytes.
    reader.read_i64()
    reader.read_byte_array()
    reader.read_byte_array()


def _skip_nullable_chat_session(reader: ByteReader) -> None:
    if reader.read_bool():
        reader.read_uuid()  # sessionId
        _skip_nullable_public_key_data(reader)


def _skip_nullable_component(reader: ByteReader) -> None:
    if reader.read_bool():
        from minebot.protocol.nbt import read_network_tag

        _, new_pos = read_network_tag(reader.data, reader.pos)
        reader.pos = new_pos


@dataclass
class PlayerListEntry:
    profile_id: uuid_module.UUID
    name: str | None = None


def parse_player_info_update(data: bytes) -> list[PlayerListEntry]:
    """Returns entries that had ADD_PLAYER data (i.e. name/uuid known).
    Other update-only actions (latency, game mode, etc.) are parsed just
    enough to keep the byte offset correct, then discarded.
    """
    reader = ByteReader(data)
    actions = _read_action_bitset(reader)
    entry_count = reader.read_varint()

    results: list[PlayerListEntry] = []

    for _ in range(entry_count):
        profile_id = reader.read_uuid()
        name: str | None = None

        for action_ordinal in sorted(actions):
            if action_ordinal == ACTION_ADD_PLAYER:
                name = reader.read_utf()
                _skip_game_profile_properties(reader)
            elif action_ordinal == ACTION_INITIALIZE_CHAT:
                _skip_nullable_chat_session(reader)
            elif action_ordinal == ACTION_UPDATE_GAME_MODE:
                reader.read_varint()
            elif action_ordinal == ACTION_UPDATE_LISTED:
                reader.read_bool()
            elif action_ordinal == ACTION_UPDATE_LATENCY:
                reader.read_varint()
            elif action_ordinal == ACTION_UPDATE_DISPLAY_NAME:
                _skip_nullable_component(reader)
            elif action_ordinal == ACTION_UPDATE_LIST_ORDER:
                reader.read_varint()
            elif action_ordinal == ACTION_UPDATE_HAT:
                reader.read_bool()

        if name is not None:
            results.append(PlayerListEntry(profile_id=profile_id, name=name))

    return results


@dataclass
class TrackedEntity:
    entity_id: int
    uuid: uuid_module.UUID
    x: float
    y: float
    z: float


@dataclass
class EntityTracker:
    """id -> position, plus a name -> uuid map maintained from the tab
    list. Fed by the PLAY loop; queried by command handlers like !follow.
    """

    by_id: dict[int, TrackedEntity] = field(default_factory=dict)
    name_to_uuid: dict[str, uuid_module.UUID] = field(default_factory=dict)

    def handle_add_entity(self, entity_id: int, entity_uuid: uuid_module.UUID, x: float, y: float, z: float) -> None:
        self.by_id[entity_id] = TrackedEntity(entity_id, entity_uuid, x, y, z)

    def handle_remove_entities(self, entity_ids: list[int]) -> None:
        for entity_id in entity_ids:
            self.by_id.pop(entity_id, None)

    def handle_absolute_position(self, entity_id: int, x: float, y: float, z: float) -> None:
        entity = self.by_id.get(entity_id)
        if entity is not None:
            entity.x, entity.y, entity.z = x, y, z

    def handle_relative_move(self, entity_id: int, dx: float, dy: float, dz: float) -> None:
        entity = self.by_id.get(entity_id)
        if entity is not None:
            entity.x += dx
            entity.y += dy
            entity.z += dz

    def handle_player_info(self, entries: list[PlayerListEntry]) -> None:
        for entry in entries:
            if entry.name is not None:
                self.name_to_uuid[entry.name] = entry.profile_id

    def find_by_name(self, name: str) -> TrackedEntity | None:
        target_uuid = self.name_to_uuid.get(name)
        if target_uuid is None:
            return None
        return self.find_by_uuid(target_uuid)

    def find_by_uuid(self, target_uuid: uuid_module.UUID) -> TrackedEntity | None:
        for entity in self.by_id.values():
            if entity.uuid == target_uuid:
                return entity
        return None


def parse_add_entity(data: bytes) -> tuple[int, uuid_module.UUID, float, float, float]:
    reader = ByteReader(data)
    entity_id = reader.read_varint()
    entity_uuid = reader.read_uuid()
    reader.read_varint()  # entity type registry id, unused
    x = reader.read_f64()
    y = reader.read_f64()
    z = reader.read_f64()
    return entity_id, entity_uuid, x, y, z


def parse_remove_entities(data: bytes) -> list[int]:
    reader = ByteReader(data)
    count = reader.read_varint()
    return [reader.read_varint() for _ in range(count)]


def parse_absolute_entity_position(data: bytes) -> tuple[int, float, float, float]:
    """Shared shape for ClientboundTeleportEntityPacket and
    ClientboundEntityPositionSyncPacket: id, then PositionMoveRotation's
    position Vec3 (we don't need deltaMovement/rotation/relatives/onGround
    for tracking purposes).
    """
    reader = ByteReader(data)
    entity_id = reader.read_varint()
    x = reader.read_f64()
    y = reader.read_f64()
    z = reader.read_f64()
    return entity_id, x, y, z


_FIXED_POINT_SCALE = 4096.0


def parse_move_entity_delta(data: bytes) -> tuple[int, float, float, float]:
    """ClientboundMoveEntityPacket.Pos/PosRot share this prefix: id, then
    xa/ya/za as i16 fixed-point deltas (1 unit = 1/4096 block).
    """
    reader = ByteReader(data)
    entity_id = reader.read_varint()
    xa = _read_i16(reader)
    ya = _read_i16(reader)
    za = _read_i16(reader)
    return entity_id, xa / _FIXED_POINT_SCALE, ya / _FIXED_POINT_SCALE, za / _FIXED_POINT_SCALE


def _read_i16(reader: ByteReader) -> int:
    value = reader.read_u16()
    return value - 0x10000 if value >= 0x8000 else value


def apply_entity_packet(tracker: EntityTracker, packet_id: int, data: bytes) -> None:
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)

    if name == "CLIENTBOUND_ADD_ENTITY":
        entity_id, entity_uuid, x, y, z = parse_add_entity(data)
        tracker.handle_add_entity(entity_id, entity_uuid, x, y, z)
        return

    if name == "CLIENTBOUND_REMOVE_ENTITIES":
        tracker.handle_remove_entities(parse_remove_entities(data))
        return

    if name in ("CLIENTBOUND_TELEPORT_ENTITY", "CLIENTBOUND_ENTITY_POSITION_SYNC"):
        entity_id, x, y, z = parse_absolute_entity_position(data)
        tracker.handle_absolute_position(entity_id, x, y, z)
        return

    if name in ("CLIENTBOUND_MOVE_ENTITY_POS", "CLIENTBOUND_MOVE_ENTITY_POS_ROT"):
        entity_id, dx, dy, dz = parse_move_entity_delta(data)
        tracker.handle_relative_move(entity_id, dx, dy, dz)
        return

    if name == "CLIENTBOUND_PLAYER_INFO_UPDATE":
        tracker.handle_player_info(parse_player_info_update(data))
        return
