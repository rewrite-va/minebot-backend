"""MicrosoftAuthenticator's cache-first behavior: a valid cached MSA
refresh token should skip the interactive device-code flow entirely; an
invalid/expired one should fall back to it. Everything past MSA auth
(Xbox/XSTS/Minecraft Services) is monkeypatched out since it's exercised
elsewhere (test_online_login_flow.py covers the real live-server-proven
path) -- this file is only about the cache decision itself.
"""

from __future__ import annotations

import uuid

import pytest

from minebot.auth import base as auth_base
from minebot.auth import msa, token_cache
from minebot.auth.base import MicrosoftAuthenticator
from minebot.auth.minecraft_services import MinecraftAuth, MinecraftProfile
from minebot.auth.xbox import XstsToken


@pytest.fixture(autouse=True)
def _patch_auth_chain(monkeypatch):
    """Stubs every step after MSA auth so tests only exercise the
    cache-vs-device-code decision, not real network calls.
    """

    async def fake_get_device_token(client, signing_key):
        return "device-token"

    async def fake_get_title_token(client, signing_key, msa_token, device_token):
        return "title-token"

    async def fake_get_xbox_user_token(client, signing_key, msa_token):
        return "user-token"

    async def fake_get_xsts_token(client, signing_key, user_token, device_token, title_token):
        return XstsToken(token="xsts-token", user_hash="userhash", expires_on="2099-01-01T00:00:00Z")

    async def fake_login_with_xbox(client, xsts):
        return MinecraftAuth(access_token="mc-access-token")

    async def fake_fetch_profile(client, access_token):
        return MinecraftProfile(username="TestPlayer", profile_id=uuid.uuid4())

    monkeypatch.setattr(auth_base.xbox, "get_device_token", fake_get_device_token)
    monkeypatch.setattr(auth_base.xbox, "get_title_token", fake_get_title_token)
    monkeypatch.setattr(auth_base.xbox, "get_xbox_user_token", fake_get_xbox_user_token)
    monkeypatch.setattr(auth_base.xbox, "get_xsts_token", fake_get_xsts_token)
    monkeypatch.setattr(auth_base.minecraft_services, "login_with_xbox", fake_login_with_xbox)
    monkeypatch.setattr(auth_base.minecraft_services, "fetch_profile", fake_fetch_profile)


@pytest.mark.asyncio
async def test_uses_cached_refresh_token_without_device_code_callback(tmp_path, monkeypatch):
    cache_path = tmp_path / "msa_token.json"
    token_cache.save_refresh_token("old-refresh-token", cache_path)

    refresh_calls = []

    async def fake_refresh(client, refresh_token):
        refresh_calls.append(refresh_token)
        return msa.MsaTokens(access_token="new-access", refresh_token="new-refresh", expires_at=0.0)

    device_code_calls = []

    async def fake_device_code(client, on_code=None):
        device_code_calls.append(True)
        return msa.MsaTokens(access_token="x", refresh_token="y", expires_at=0.0)

    monkeypatch.setattr(msa, "refresh_msa_tokens", fake_refresh)
    monkeypatch.setattr(msa, "authenticate_device_code", fake_device_code)

    authenticator = MicrosoftAuthenticator(cache_path=cache_path)
    profile = await authenticator.get_profile()

    assert refresh_calls == ["old-refresh-token"]
    assert device_code_calls == []  # never fell back to interactive sign-in
    assert profile.username == "TestPlayer"

    # The (possibly rotated) refresh token gets saved back for next time.
    assert token_cache.load_refresh_token(cache_path) == "new-refresh"


@pytest.mark.asyncio
async def test_falls_back_to_device_code_when_cache_is_invalid(tmp_path, monkeypatch):
    cache_path = tmp_path / "msa_token.json"
    token_cache.save_refresh_token("stale-refresh-token", cache_path)

    async def fake_refresh_fails(client, refresh_token):
        raise msa.MsaAuthError("refresh token expired")

    device_code_calls = []

    async def fake_device_code(client, on_code=None):
        device_code_calls.append(True)
        return msa.MsaTokens(access_token="fresh-access", refresh_token="fresh-refresh", expires_at=0.0)

    monkeypatch.setattr(msa, "refresh_msa_tokens", fake_refresh_fails)
    monkeypatch.setattr(msa, "authenticate_device_code", fake_device_code)

    authenticator = MicrosoftAuthenticator(cache_path=cache_path)
    profile = await authenticator.get_profile()

    assert device_code_calls == [True]
    assert profile.username == "TestPlayer"
    assert token_cache.load_refresh_token(cache_path) == "fresh-refresh"


@pytest.mark.asyncio
async def test_no_cache_file_goes_straight_to_device_code(tmp_path, monkeypatch):
    cache_path = tmp_path / "does_not_exist.json"

    refresh_calls = []

    async def fake_refresh(client, refresh_token):
        refresh_calls.append(refresh_token)
        return msa.MsaTokens(access_token="x", refresh_token="y", expires_at=0.0)

    device_code_calls = []

    async def fake_device_code(client, on_code=None):
        device_code_calls.append(True)
        return msa.MsaTokens(access_token="fresh-access", refresh_token="fresh-refresh", expires_at=0.0)

    monkeypatch.setattr(msa, "refresh_msa_tokens", fake_refresh)
    monkeypatch.setattr(msa, "authenticate_device_code", fake_device_code)

    authenticator = MicrosoftAuthenticator(cache_path=cache_path)
    await authenticator.get_profile()

    assert refresh_calls == []
    assert device_code_calls == [True]
