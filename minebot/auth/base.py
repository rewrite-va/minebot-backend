"""Auth interface, stubbed. Server is online-mode (see FINDINGS.md), so a
real implementation needs: MSA device-code OAuth -> Xbox Live -> XSTS ->
Minecraft Services token -> game profile, plus a Mojang sessionserver
`joinServer` call once the login encryption handshake starts. None of that
is implemented yet; this stub lets the connection/bot skeleton be built and
tested against an offline-mode server in the meantime.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Protocol


def offline_player_uuid(username: str) -> uuid.UUID:
    """Matches vanilla's UUID.nameUUIDFromBytes("OfflinePlayer:" + name):
    an MD5-based UUIDv3, but over the raw name bytes directly rather than
    Python's uuid.uuid3 (which hashes namespace-bytes + name together, a
    different input and thus a different result).
    """
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(digest))


@dataclass(frozen=True)
class GameProfile:
    username: str
    profile_id: uuid.UUID
    access_token: str | None = None  # None in offline mode


class Authenticator(Protocol):
    async def get_profile(self) -> GameProfile:
        """Returns the profile to log in with."""
        ...

    async def join_server(self, server_id_hash: str) -> None:
        """Called after computing the server hash from ClientboundHelloPacket,
        before replying with ServerboundKeyPacket. Must call Mojang's
        sessionserver `joinServer` endpoint with access_token in online mode.
        No-op in offline mode.
        """
        ...


class OfflineAuthenticator:
    """Offline-mode auth: fabricate a deterministic UUID from the username,
    matching vanilla's own offline-UUID derivation
    (UUID.nameUUIDFromBytes("OfflinePlayer:" + name)), and skip the
    session-server join call entirely.
    """

    def __init__(self, username: str):
        self.username = username

    async def get_profile(self) -> GameProfile:
        return GameProfile(
            username=self.username,
            profile_id=offline_player_uuid(self.username),
            access_token=None,
        )

    async def join_server(self, server_id_hash: str) -> None:
        return None


class MicrosoftAuthenticator:
    """Not implemented yet. See FINDINGS.md "Auth requirement" section for
    the flow this needs to perform:

      1. MSA device-code flow (user visits a URL, enters a code)
      2. Xbox Live authentication -> XSTS token
      3. Minecraft Services token exchange -> game profile (uuid + username)
      4. sessionserver.mojang.com `joinServer` call using the server hash
         computed from ClientboundHelloPacket, before ServerboundKeyPacket

    Raises NotImplementedError until built; use OfflineAuthenticator for
    now when testing against an offline-mode server.
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "Microsoft auth is not implemented yet; the target server "
            "(51.81.166.195:60433) is online-mode and won't accept "
            "OfflineAuthenticator. See FINDINGS.md for the required flow."
        )

    async def get_profile(self) -> GameProfile:
        raise NotImplementedError

    async def join_server(self, server_id_hash: str) -> None:
        raise NotImplementedError
