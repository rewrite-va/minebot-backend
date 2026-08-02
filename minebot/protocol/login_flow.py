"""Drives the connection from a fresh TCP socket through LOGIN to the start
of CONFIGURATION.

Encryption (the ClientboundHello -> ServerboundKey exchange) only happens if
the server is online-mode. See minebot/auth/encryption.py for the algorithm
details (server-hash computation, RSA wrapping, AES/CFB8 cipher setup).
"""

from __future__ import annotations

import logging

from minebot.auth.base import Authenticator
from minebot.auth.encryption import (
    compute_server_hash,
    encrypt_with_server_key,
    generate_shared_secret,
    load_server_public_key,
    make_cfb8_cipher,
)
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
    send_key,
    send_login_acknowledged,
)
from minebot.protocol.registry import REGISTRY

PROTOCOL_VERSION = REGISTRY.protocol_version
log = logging.getLogger("minebot.login_flow")


class LoginError(Exception):
    pass


async def perform_login(conn: Connection, host: str, port: int, authenticator: Authenticator) -> LoginFinished:
    profile = await authenticator.get_profile()

    await send_handshake(conn, PROTOCOL_VERSION, host, port, ClientIntent.LOGIN)
    await send_hello(conn, profile.username, profile.profile_id)

    while True:
        raw = await conn.read_packet()
        log.debug("got LOGIN packet id=%d data_len=%d", raw.packet_id, len(raw.data))
        parsed = parse_login_clientbound(raw.packet_id, raw.data)

        if isinstance(parsed, LoginDisconnect):
            raise LoginError(f"disconnected during login: {parsed.reason}")

        if isinstance(parsed, ClientboundHello):
            await _do_encryption_handshake(conn, parsed, authenticator)
            continue

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


async def _do_encryption_handshake(conn: Connection, hello: ClientboundHello, authenticator: Authenticator) -> None:
    public_key = load_server_public_key(hello.public_key)
    shared_secret = generate_shared_secret()

    log.debug("received ClientboundHello: should_authenticate=%s", hello.should_authenticate)

    if hello.should_authenticate:
        server_hash = compute_server_hash(hello.server_id, shared_secret, hello.public_key)
        await authenticator.join_server(server_hash)
        log.info("sessionserver join succeeded")

    encrypted_secret = encrypt_with_server_key(public_key, shared_secret)
    encrypted_challenge = encrypt_with_server_key(public_key, hello.challenge)
    await send_key(conn, encrypted_secret, encrypted_challenge)

    encryptor, decryptor = make_cfb8_cipher(shared_secret)
    conn.set_encryption(decryptor, encryptor)
