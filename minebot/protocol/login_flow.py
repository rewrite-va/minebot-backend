"""Drives the connection from a fresh TCP socket through LOGIN to the start
of CONFIGURATION.

Encryption (the ClientboundHello -> ServerboundKey exchange) is only
required in online mode. That branch is not implemented yet — see
FINDINGS.md and minebot/auth/base.py. Against an offline-mode server this
completes end-to-end.
"""

from __future__ import annotations

from minebot.auth.base import Authenticator
from minebot.net.connection import Connection
from minebot.protocol.handshake import (
    ClientIntent,
    ClientboundHello,
    LoginCompression,
    LoginDisconnect,
    LoginFinished,
    parse_login_clientbound,
    send_handshake,
    send_hello,
    send_login_acknowledged,
)
from minebot.protocol.registry import REGISTRY

PROTOCOL_VERSION = REGISTRY.protocol_version


class LoginError(Exception):
    pass


async def perform_login(conn: Connection, host: str, port: int, authenticator: Authenticator) -> LoginFinished:
    profile = await authenticator.get_profile()

    await send_handshake(conn, PROTOCOL_VERSION, host, port, ClientIntent.LOGIN)
    await send_hello(conn, profile.username, profile.profile_id)

    while True:
        raw = await conn.read_packet()
        parsed = parse_login_clientbound(raw.packet_id, raw.data)

        if isinstance(parsed, LoginDisconnect):
            raise LoginError(f"disconnected during login: {parsed.reason}")

        if isinstance(parsed, ClientboundHello):
            # Online-mode encryption handshake. Requires: RSA-encrypt a
            # fresh AES secret + the server's challenge with parsed.public_key,
            # send ServerboundKeyPacket, switch the Connection to AES/CFB8
            # both ways, then call authenticator.join_server(server_hash)
            # using Mojang's server-hash algorithm (sha1 of serverId +
            # secret + publicKey, mojang's quirky signed-hex digest) before
            # the server will let us proceed. None of this is built yet.
            raise NotImplementedError(
                "server requested the online-mode encryption handshake; "
                "MicrosoftAuthenticator / encryption is not implemented yet "
                "(see FINDINGS.md)"
            )

        if isinstance(parsed, LoginCompression):
            conn.enable_compression(parsed.threshold)
            continue

        if isinstance(parsed, LoginFinished):
            await send_login_acknowledged(conn)
            return parsed

        if parsed is None:
            # Unhandled packet (custom query, cookie request, ...). Not
            # needed for the MVP path against a vanilla server; ignore.
            continue
