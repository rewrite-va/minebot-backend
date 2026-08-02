from __future__ import annotations

import asyncio
import logging
import os

from minebot.auth.base import MicrosoftAuthenticator, OfflineAuthenticator
from minebot.bot.movement import MovementController, register_movement_commands
from minebot.bot.play_loop import run_play_loop
from minebot.commands.registry import CommandRegistry
from minebot.config import BotConfig
from minebot.net.connection import Connection
from minebot.protocol.chunks import ChunkHeightmapCache
from minebot.protocol.configuration import run_configuration_phase
from minebot.protocol.entities import EntityTracker
from minebot.protocol.login_flow import perform_login

# MINEBOT_LOG_LEVEL lets a troubleshooting session flip on debug logging
# (e.g. entity-tracking packet visibility in bot/play_loop.py) without a
# code change: `MINEBOT_LOG_LEVEL=DEBUG uv run python -m minebot.main`.
logging.basicConfig(level=os.environ.get("MINEBOT_LOG_LEVEL", "INFO").upper())
log = logging.getLogger("minebot")


async def _print_device_code(user_code: str, verification_uri: str) -> None:
    log.info("Microsoft sign-in required: open %s and enter code %s", verification_uri, user_code)


async def run(config: BotConfig) -> None:
    authenticator = (
        MicrosoftAuthenticator(on_device_code=_print_device_code)
        if config.online_mode
        else OfflineAuthenticator(config.username)
    )

    if config.online_mode:
        log.info("connecting to %s:%s (online-mode; identity comes from Microsoft sign-in)", config.host, config.port)
    else:
        log.info("connecting to %s:%s as %s", config.host, config.port, config.username)
    conn = await Connection.open(config.host, config.port)

    login_result = await perform_login(conn, config.host, config.port, authenticator)
    log.info("login finished: %s (%s)", login_result.username, login_result.profile_id)

    await run_configuration_phase(conn)
    log.info("configuration finished, entering play phase")

    registry = CommandRegistry()
    tracker = EntityTracker()
    heightmaps = ChunkHeightmapCache()
    movement = MovementController(tracker)
    register_movement_commands(registry, movement)

    # Mining/placing/combat/inventory command handlers are not implemented
    # yet -- chat receive/dispatch, keepalive, and basic movement
    # (forward/backward/left/right/follow/stop) are functional.
    await run_play_loop(conn, registry, movement, tracker, heightmaps)


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
