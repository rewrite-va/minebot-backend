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
from pathlib import Path
from typing import Protocol

import httpx

from minebot.auth import minecraft_services, msa, token_cache, xbox
from minebot.auth.msa import DeviceCodeCallback, MsaAuthError

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

    Caches the MSA refresh token on disk (see minebot/auth/token_cache.py)
    so repeat runs can skip the interactive device-code sign-in, matching
    how real Minecraft launchers behave -- sign in once, then silently
    refresh. Only the MSA refresh token is persisted; Xbox/XSTS/Minecraft
    tokens are cheap to re-derive each run and aren't cached (see
    token_cache.py's docstring for why).
    """

    def __init__(
        self,
        on_device_code: DeviceCodeCallback | None = None,
        cache_path: Path = token_cache.DEFAULT_CACHE_PATH,
    ):
        self._on_device_code = on_device_code
        self._cache_path = cache_path
        self._client = httpx.AsyncClient(timeout=30.0)
        self._minecraft_access_token: str | None = None
        self._profile_id: uuid.UUID | None = None

    async def _get_msa_tokens(self) -> msa.MsaTokens:
        cached_refresh_token = token_cache.load_refresh_token(self._cache_path)
        if cached_refresh_token is not None:
            try:
                log.info("found a cached sign-in, refreshing it (no browser step needed)")
                return await msa.refresh_msa_tokens(self._client, cached_refresh_token)
            except MsaAuthError:
                log.info("cached sign-in is no longer valid, falling back to a fresh sign-in")

        return await (
            msa.authenticate_device_code(self._client, self._on_device_code)
            if self._on_device_code
            else msa.authenticate_device_code(self._client)
        )

    async def get_profile(self) -> GameProfile:
        msa_tokens = await self._get_msa_tokens()
        token_cache.save_refresh_token(msa_tokens.refresh_token, self._cache_path)

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
