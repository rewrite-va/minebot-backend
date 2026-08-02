"""Microsoft Account device-code auth via the legacy login.live.com
endpoints, using Microsoft's own official Minecraft Nintendo Switch "Title
ID" (00000000441cc96b) as the client identifier.

Why this instead of a standalone Azure AD app: we first tried
prismarine-auth's documented fallback for third-party apps -- an Azure AD
app client id (389b1b32-b5d5-43b2-bddc-84ce938d6737, originally from a tool
called Office365APIEditor) -- and it came back `AADSTS700016: Application
... was not found in the directory`, i.e. deregistered/deleted since that
library's code was written. prismarine-auth's actual *default* flow doesn't
use an Azure app at all: it authenticates as an official Microsoft-owned
title via a Title ID Microsoft controls directly and won't let go stale
(see prismarine-auth's Titles export: `MinecraftNintendoSwitch =
'00000000441cc96b'`, used with its 'live' flow / LiveTokenManager against
login.live.com rather than login.microsoftonline.com's v2.0 endpoints).

Important: prismarine-auth's own docs/API.md is explicit that `MinecraftJava`
(00000000402b5328) does NOT work with the `live` flow's device+title token
dance at all -- it only works with the separate `sisu` flow. `live` +
device/title auth is documented as pairing with `MinecraftNintendoSwitch` +
`deviceType: 'Nintendo'`. We initially used the Java title ID here by
mistake, which made `title.auth.xboxlive.com/title/authenticate` reject the
device token with a bare 400 (device token issued under a title the title-auth
endpoint doesn't recognize as valid for this flow) -- switched to the Switch
title ID + Nintendo device type to match what prismarine-auth's docs
actually specify. This is what mindcraft/mineflayer use in practice via
their prismarine-auth dependency. Ported from LiveTokenManager.js (see
FINDINGS.md for the full trace of how we found this).

Note this is a different, older device-code dialect than RFC 8628 (no
`scope`+`client_id` JSON devicecode response with a `verification_uri_complete`
etc. -- just `response_type=device_code` form-encoded against
oauth20_connect.srf, matching this specific endpoint's contract) and requires
carrying the request's Set-Cookie headers through to the polling calls,
which the modern Azure v2.0 endpoints don't need.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

MINECRAFT_NINTENDO_SWITCH_TITLE_ID = "00000000441cc96b"
SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"

DEVICE_CODE_URL = "https://login.live.com/oauth20_connect.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"


@dataclass(frozen=True)
class MsaTokens:
    access_token: str
    refresh_token: str
    expires_at: float  # time.monotonic()-based deadline


class MsaAuthError(Exception):
    pass


DeviceCodeCallback = Callable[[str, str], Awaitable[None] | None]


async def _default_device_code_callback(user_code: str, verification_uri: str) -> None:
    print(f"[msa] To sign in, open {verification_uri} and enter code: {user_code}")


async def authenticate_device_code(
    client: httpx.AsyncClient,
    on_code: DeviceCodeCallback = _default_device_code_callback,
) -> MsaTokens:
    response = await client.post(
        DEVICE_CODE_URL,
        data={
            "client_id": MINECRAFT_NINTENDO_SWITCH_TITLE_ID,
            "scope": SCOPE,
            "response_type": "device_code",
        },
    )
    if response.status_code != 200:
        raise MsaAuthError(f"failed to request device code: {response.status_code} {response.text}")

    # Carry the session cookies this endpoint sets through to the polling
    # calls -- login.live.com ties the device-code session to them.
    cookies = response.cookies

    device_code_data = response.json()

    callback_result = on_code(device_code_data["user_code"], device_code_data["verification_uri"])
    if asyncio.iscoroutine(callback_result):
        await callback_result

    interval = device_code_data.get("interval", 5)
    expires_in = device_code_data.get("expires_in", 900)
    deadline = time.monotonic() + expires_in

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)

        token_response = await client.post(
            f"{TOKEN_URL}?client_id={MINECRAFT_NINTENDO_SWITCH_TITLE_ID}",
            data={
                "client_id": MINECRAFT_NINTENDO_SWITCH_TITLE_ID,
                "device_code": device_code_data["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            cookies=cookies,
        )
        payload = token_response.json()

        if token_response.status_code == 200 and "error" not in payload:
            return _tokens_from_response(payload)

        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "authorization_declined":
            raise MsaAuthError("user declined the sign-in request")
        if error == "expired_token":
            raise MsaAuthError("device code expired before the user signed in")
        raise MsaAuthError(f"device code polling failed: {payload}")

    raise MsaAuthError("device code expired before the user signed in")


async def refresh_msa_tokens(client: httpx.AsyncClient, refresh_token: str) -> MsaTokens:
    response = await client.post(
        TOKEN_URL,
        data={
            "client_id": MINECRAFT_NINTENDO_SWITCH_TITLE_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
    )
    if response.status_code != 200:
        raise MsaAuthError(f"failed to refresh MSA token: {response.text}")
    return _tokens_from_response(response.json())


def _tokens_from_response(payload: dict) -> MsaTokens:
    return MsaTokens(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=time.monotonic() + payload.get("expires_in", 3600),
    )
