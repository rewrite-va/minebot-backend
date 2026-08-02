"""Auth interface. Server is online-mode (see FINDINGS.md): real Microsoft
auth needs MSA device-code OAuth -> Xbox Live -> XSTS -> Minecraft Services
token -> game profile, plus a Mojang sessionserver `joinServer` call once
the login encryption handshake starts. See minebot/auth/msa.py, xbox.py,
minecraft_services.py, and encryption.py for each piece.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx

from minebot.auth import minecraft_services, msa, xbox
from minebot.auth.msa import DeviceCodeCallback

log = logging.getLogger("minebot.auth")


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
    """Real online-mode auth: MSA device-code flow -> Xbox Live -> XSTS ->
    Minecraft Services -> game profile, then a sessionserver `joinServer`
    call when the login encryption handshake asks for it (see
    minebot/auth/encryption.py, called from protocol/login_flow.py).

    No token caching to disk yet: every run re-does the device-code flow,
    which is disruptive (the user has to re-approve each time) but correct
    and simple. Add a cache (e.g. matching prismarine-auth's per-token-type
    JSON file cache) if this becomes annoying in practice.
    """

    def __init__(self, on_device_code: DeviceCodeCallback | None = None):
        self._on_device_code = on_device_code
        self._client = httpx.AsyncClient(timeout=30.0)
        self._minecraft_access_token: str | None = None
        self._profile_id: uuid.UUID | None = None

    async def get_profile(self) -> GameProfile:
        msa_tokens = await (
            msa.authenticate_device_code(self._client, self._on_device_code)
            if self._on_device_code
            else msa.authenticate_device_code(self._client)
        )

        signing_key = xbox.XboxSigningKey()
        device_token = await xbox.get_device_token(self._client, signing_key)
        title_token = await xbox.get_title_token(self._client, signing_key, msa_tokens.access_token, device_token)
        user_token = await xbox.get_xbox_user_token(self._client, signing_key, msa_tokens.access_token)
        xsts = await xbox.get_xsts_token(self._client, signing_key, user_token, device_token, title_token)

        mc_auth = await minecraft_services.login_with_xbox(self._client, xsts)
        self._minecraft_access_token = mc_auth.access_token

        profile = await minecraft_services.fetch_profile(self._client, mc_auth.access_token)
        self._profile_id = profile.profile_id
        log.info("authenticated as %s (%s)", profile.username, profile.profile_id)
        return GameProfile(
            username=profile.username,
            profile_id=profile.profile_id,
            access_token=mc_auth.access_token,
        )

    async def join_server(self, server_id_hash: str) -> None:
        if self._minecraft_access_token is None or self._profile_id is None:
            raise RuntimeError("join_server called before get_profile() completed authentication")

        from minebot.auth.encryption import join_server as do_join_server

        await do_join_server(
            self._client,
            self._minecraft_access_token,
            str(self._profile_id),
            server_id_hash,
        )

    async def close(self) -> None:
        await self._client.aclose()
