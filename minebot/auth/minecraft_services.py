"""Exchanges an XSTS token for a Minecraft access token + game profile via
api.minecraftservices.com. Field/endpoint shapes ported from
prismarine-auth's MinecraftJavaTokenManager (see FINDINGS.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from minebot.auth.xbox import XstsToken

LOGIN_WITH_XBOX_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"


@dataclass(frozen=True)
class MinecraftAuth:
    access_token: str


@dataclass(frozen=True)
class MinecraftProfile:
    username: str
    profile_id: uuid.UUID


class MinecraftServicesError(Exception):
    pass


async def login_with_xbox(client: httpx.AsyncClient, xsts: XstsToken) -> MinecraftAuth:
    response = await client.post(
        LOGIN_WITH_XBOX_URL,
        json={"identityToken": f"XBL3.0 x={xsts.user_hash};{xsts.token}"},
    )
    if response.status_code != 200:
        raise MinecraftServicesError(f"Minecraft Services login failed: {response.status_code} {response.text}")
    return MinecraftAuth(access_token=response.json()["access_token"])


async def fetch_profile(client: httpx.AsyncClient, access_token: str) -> MinecraftProfile:
    response = await client.get(
        PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code != 200:
        # Surface Mojang's actual response body rather than guessing why it
        # failed -- a 404 here can mean several different things (no
        # entitlement, entitlement present but no profile/username created
        # yet at minecraft.net, a very recent purchase not yet propagated),
        # and the response body usually says which.
        raise MinecraftServicesError(
            f"failed to fetch Minecraft profile: {response.status_code} {response.text}"
        )

    data = response.json()
    return MinecraftProfile(username=data["name"], profile_id=uuid.UUID(data["id"]))
