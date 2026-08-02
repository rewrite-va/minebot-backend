"""PLAY-phase main loop: reads packets forever, answers keepalives, tracks
our own position and other entities, and feeds chat text through the
command registry. This is the MVP loop from prompt.txt (connect, listen for
chat, parse commands) plus basic movement/follow -- mining/placing/combat
still need more PLAY-phase packets not built yet.

Also handles death/respawn (see protocol/health.py): found via live testing
that dying (a baby zombie killed the bot) left it stuck forever -- we never
requested a respawn, so the server-side player entity just stayed dead
(explaining why a subsequent /tp didn't make the bot visibly reappear: a
dead, unrespawned player isn't a normal tickable entity to other clients).

Also found: CLIENTBOUND_PLAYER_COMBAT_KILL is NOT sent for every death --
the vanilla client's own game loop actually detects death by polling
`player.isDeadOrDying()` (health <= 0) every tick, set from
ClientboundSetHealthPacket, not from a dedicated "you died" packet (see
Minecraft.java's tick loop in the decompiled source). Relying only on
COMBAT_KILL missed a real death in live testing. Now trigger respawn from
CLIENTBOUND_SET_HEALTH reaching 0 as well, matching what the real client
does; COMBAT_KILL is kept as a second trigger since it's harmless if it
also fires (guarded by `awaiting_respawn` so we don't send PERFORM_RESPAWN
twice for the same death).
"""

from __future__ import annotations

import logging

from minebot.bot.movement import MovementController
from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection, STATE_PLAY
from minebot.protocol.chat import PlayerChatMessage, SystemChatMessage, parse_play_chat, send_say
from minebot.protocol.chunk_blocks import ChunkBlockCache, parse_level_chunk_block_data
from minebot.protocol.chunks import ChunkHeightmapCache, parse_level_chunk_with_light
from minebot.protocol.entities import EntityTracker, apply_entity_packet
from minebot.protocol.health import (
    parse_own_entity_id,
    parse_player_combat_kill,
    parse_respawn,
    parse_set_health,
    send_perform_respawn,
)
from minebot.protocol.keepalive import parse_keepalive, parse_ping, send_keepalive_response, send_pong
from minebot.protocol.movement import parse_player_position, send_accept_teleportation
from minebot.protocol.registry import REGISTRY

log = logging.getLogger("minebot.play")


async def run_play_loop(
    conn: Connection,
    commands: CommandRegistry,
    movement: MovementController,
    tracker: EntityTracker,
    heightmaps: ChunkHeightmapCache,
    blocks: ChunkBlockCache,
) -> None:
    own_entity_id: int | None = None
    awaiting_respawn = False

    async def _die_and_request_respawn() -> None:
        nonlocal awaiting_respawn
        if awaiting_respawn:
            return
        awaiting_respawn = True
        log.info("we died, requesting respawn")
        movement.mark_position_stale()
        await send_perform_respawn(conn)

    while True:
        raw = await conn.read_packet()

        own_entity_info = parse_own_entity_id(raw.packet_id, raw.data)
        if own_entity_info is not None:
            own_entity_id = own_entity_info.entity_id
            log.info("own entity id: %d", own_entity_id)
            continue

        set_health = parse_set_health(raw.packet_id, raw.data)
        if set_health is not None:
            if set_health.health <= 0.0:
                await _die_and_request_respawn()
            continue

        combat_kill = parse_player_combat_kill(raw.packet_id, raw.data)
        if combat_kill is not None:
            if combat_kill.player_id == own_entity_id:
                await _die_and_request_respawn()
            continue

        respawned = parse_respawn(raw.packet_id, raw.data)
        if respawned is not None:
            log.info("respawned")
            awaiting_respawn = False
            movement.mark_position_stale()
            continue

        keepalive_id = parse_keepalive(STATE_PLAY, raw.packet_id, raw.data)
        if keepalive_id is not None:
            await send_keepalive_response(conn, STATE_PLAY, keepalive_id)
            continue

        ping_id = parse_ping(STATE_PLAY, raw.packet_id, raw.data)
        if ping_id is not None:
            await send_pong(conn, STATE_PLAY, ping_id)
            continue

        position_sync = parse_player_position(raw.packet_id, raw.data)
        if position_sync is not None:
            log.info(
                "position sync: (%.4f, %.4f, %.4f) yaw=%.2f teleport_id=%d relatives=%s",
                position_sync.x, position_sync.y, position_sync.z, position_sync.yaw,
                position_sync.teleport_id, bin(position_sync.relatives),
            )
            movement.sync_from_position_packet(position_sync)
            await send_accept_teleportation(conn, position_sync.teleport_id)
            continue

        chat = parse_play_chat(raw.packet_id, raw.data)
        if isinstance(chat, PlayerChatMessage):
            log.info("<%s> %s", chat.sender, chat.content)
            dispatched = await commands.dispatch(chat.content, conn, chat.sender)
            if not dispatched and chat.content.strip().startswith("!"):
                await send_say(conn, f"unknown command: {chat.content}")
            continue

        if isinstance(chat, SystemChatMessage):
            log.info("[system] %s", chat.content)
            await commands.dispatch(chat.content, conn, None)
            continue

        chunk_heightmap = parse_level_chunk_with_light(raw.packet_id, raw.data)
        if chunk_heightmap is not None:
            heightmaps.handle_chunk(chunk_heightmap)
            chunk_blocks = parse_level_chunk_block_data(raw.packet_id, raw.data)
            if chunk_blocks is not None:
                blocks.handle_chunk(chunk_blocks)
            continue

        if log.isEnabledFor(logging.DEBUG):
            name = REGISTRY.name_for(STATE_PLAY, "clientbound", raw.packet_id)
            if name in ("CLIENTBOUND_ADD_ENTITY", "CLIENTBOUND_REMOVE_ENTITIES", "CLIENTBOUND_PLAYER_INFO_UPDATE"):
                log.debug("entity packet: %s", name)
        apply_entity_packet(tracker, raw.packet_id, raw.data)

        # Everything else (inventory, ...) is not handled yet; safe to ignore.
