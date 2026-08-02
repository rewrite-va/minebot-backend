from __future__ import annotations

import asyncio
import logging

from minebot.auth.base import MicrosoftAuthenticator, OfflineAuthenticator
from minebot.bot.movement import MovementController, register_movement_commands
from minebot.bot.play_loop import run_play_loop
from minebot.commands.registry import CommandRegistry
from minebot.config import BotConfig
from minebot.net.connection import Connection
from minebot.protocol.configuration import run_configuration_phase
from minebot.protocol.login_flow import perform_login

logging.basicConfig(level=logging.INFO)
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
    movement = MovementController()
    register_movement_commands(registry, movement)

    # Movement/mining/placing/combat/inventory command handlers still raise
    # NotImplementedError when invoked (see bot/movement.py) -- only chat
    # receive, command parsing/dispatch, and keepalive are functional so far.
    await run_play_loop(conn, registry)


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
