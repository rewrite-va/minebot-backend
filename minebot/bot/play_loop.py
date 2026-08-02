"""PLAY-phase main loop: reads packets forever, answers keepalives, and
feeds chat text through the command registry. This is the MVP loop from
prompt.txt (connect, listen for chat, parse commands) minus movement/mining/
placing/combat, which need more PLAY-phase packets not built yet.
"""

from __future__ import annotations

import logging

from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection, STATE_PLAY
from minebot.protocol.chat import PlayerChatMessage, SystemChatMessage, parse_play_chat, send_say
from minebot.protocol.keepalive import parse_keepalive, parse_ping, send_keepalive_response, send_pong

log = logging.getLogger("minebot.play")


async def run_play_loop(conn: Connection, commands: CommandRegistry) -> None:
    while True:
        raw = await conn.read_packet()

        keepalive_id = parse_keepalive(STATE_PLAY, raw.packet_id, raw.data)
        if keepalive_id is not None:
            await send_keepalive_response(conn, STATE_PLAY, keepalive_id)
            continue

        ping_id = parse_ping(STATE_PLAY, raw.packet_id, raw.data)
        if ping_id is not None:
            await send_pong(conn, STATE_PLAY, ping_id)
            continue

        chat = parse_play_chat(raw.packet_id, raw.data)
        if isinstance(chat, PlayerChatMessage):
            log.info("<%s> %s", chat.sender, chat.content)
            dispatched = await commands.dispatch(chat.content, conn)
            if not dispatched and chat.content.strip().startswith("!"):
                await send_say(conn, f"unknown command: {chat.content}")
            continue

        if isinstance(chat, SystemChatMessage):
            log.info("[system] %s", chat.content)
            await commands.dispatch(chat.content, conn)
            continue

        # Everything else (entity updates, chunk data, inventory, ...) is
        # not handled yet; safe to ignore for the MVP.
