from __future__ import annotations

import asyncio
import logging

from minebot.auth.base import MicrosoftAuthenticator, OfflineAuthenticator
from minebot.bot.movement import MovementController, register_movement_commands
from minebot.commands.registry import CommandRegistry
from minebot.config import BotConfig
from minebot.net.connection import Connection
from minebot.protocol.login_flow import perform_login

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("minebot")


async def run(config: BotConfig) -> None:
    authenticator = (
        MicrosoftAuthenticator() if config.online_mode else OfflineAuthenticator(config.username)
    )

    log.info("connecting to %s:%s as %s", config.host, config.port, config.username)
    conn = await Connection.open(config.host, config.port)

    login_result = await perform_login(conn, config.host, config.port, authenticator)
    log.info("login finished: %s (%s)", login_result.username, login_result.profile_id)

    registry = CommandRegistry()
    movement = MovementController()
    register_movement_commands(registry, movement)

    # CONFIGURATION-phase handling (registry data, known packs, the new
    # code-of-conduct accept step, ClientboundFinishConfiguration ->
    # ServerboundFinishConfiguration) and the PLAY-phase chat listen loop
    # are not implemented yet. See FINDINGS.md for the packet names/order
    # already extracted from source.
    raise NotImplementedError(
        "login succeeded; CONFIGURATION and PLAY phase handling is not built yet"
    )


def main() -> None:
    config = BotConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
