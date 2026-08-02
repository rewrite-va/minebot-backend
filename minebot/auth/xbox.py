"""Xbox Live user/device/title token + XSTS token exchange.

Ported from prismarine-auth's XboxTokenManager (see FINDINGS.md for the
source read, and minebot/auth/msa.py's docstring for why we're on the
"live"/Title-ID auth path rather than a standalone Azure AD app). Two
things that aren't obvious from the wiki.vg-level description of this flow
and are easy to get wrong:

1. Xbox Live's user/device/title/xsts auth endpoints require every request
   to be signed with an ephemeral ES256 (P-256) EC keypair generated once
   per auth session, sent as a JWK "ProofKey" in the payload and a base64
   "Signature" header over a specific byte layout (policy version +
   Windows-epoch timestamp + method + path + auth-token + body, big-endian,
   NUL-terminated strings). Skipping this makes Xbox Live reject the
   request outright.
2. The RpsTicket preamble is "t=" (not "d=") for tokens obtained via the
   legacy login.live.com device-code flow with a Title ID (our case) --
   "d=" is for tokens from a real Azure AD app registration instead.
3. Because we're using a Title ID (MinecraftJava, see msa.py), Xbox Live
   requires the extra device-token and title-token steps before the user
   token and XSTS calls will succeed; a plain Azure-AD-app flow can skip
   both and go straight to getUserToken -> getXSTSToken.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives import hashes

USER_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTHORIZE_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
DEVICE_AUTH_URL = "https://device.auth.xboxlive.com/device/authenticate"
TITLE_AUTH_URL = "https://title.auth.xboxlive.com/title/authenticate"

# Windows FILETIME epoch (1601-01-01) is this many seconds before the Unix epoch.
WINDOWS_EPOCH_OFFSET_SECONDS = 11644473600


@dataclass(frozen=True)
class XstsToken:
    token: str
    user_hash: str
    expires_on: str


class XboxAuthError(Exception):
    pass


class XboxSigningKey:
    """Ephemeral ES256 keypair used to sign every Xbox Live request and to
    prove possession of it via the "ProofKey" JWK in each payload.
    """

    def __init__(self) -> None:
        self._private_key = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict:
        public_numbers = self._private_key.public_key().public_numbers()
        x_bytes = public_numbers.x.to_bytes(32, "big")
        y_bytes = public_numbers.y.to_bytes(32, "big")
        return {
            "kty": "EC",
            "crv": "P-256",
            "alg": "ES256",
            "use": "sig",
            "x": _b64url(x_bytes),
            "y": _b64url(y_bytes),
        }

    def sign_request(self, url: str, body: bytes) -> bytes:
        windows_timestamp = (int(time.time()) + WINDOWS_EPOCH_OFFSET_SECONDS) * 10_000_000
        path_and_query = httpx.URL(url).raw_path.decode("ascii")

        buf = bytearray()
        buf += struct.pack(">i", 1)  # policy version
        buf += b"\x00"
        buf += struct.pack(">Q", windows_timestamp)
        buf += b"\x00"
        buf += b"POST\x00"
        buf += path_and_query.encode("ascii") + b"\x00"
        buf += b"\x00"  # authorization token (always empty for these calls)
        buf += bytes(body) + b"\x00"

        der_signature = self._private_key.sign(bytes(buf), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

        header = struct.pack(">i", 1) + struct.pack(">Q", windows_timestamp) + raw_signature
        return header


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def get_xbox_user_token(client: httpx.AsyncClient, signing_key: XboxSigningKey, msa_access_token: str) -> str:
    payload = {
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": f"t={msa_access_token}",
        },
    }
    body = _json_bytes(payload)
    signature = signing_key.sign_request(USER_AUTH_URL, body)

    response = await client.post(
        USER_AUTH_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "2",
            "Signature": _b64_std(signature),
        },
    )
    if response.status_code != 200:
        raise XboxAuthError(f"Xbox Live user token request failed: {response.status_code} {response.text}")

    return response.json()["Token"]


async def get_device_token(client: httpx.AsyncClient, signing_key: XboxSigningKey) -> str:
    import uuid as uuid_module

    device_id = str(uuid_module.uuid4())
    payload = {
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
        "Properties": {
            "AuthMethod": "ProofOfPossession",
            "Id": f"{{{device_id}}}",
            "DeviceType": "Nintendo",
            "SerialNumber": f"{{{str(uuid_module.uuid4())}}}",
            "Version": "0.0.0",
            "ProofKey": signing_key.jwk(),
        },
    }
    body = _json_bytes(payload)
    signature = signing_key.sign_request(DEVICE_AUTH_URL, body)

    response = await client.post(
        DEVICE_AUTH_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "1",
            "Signature": _b64_std(signature),
        },
    )
    if response.status_code != 200:
        raise XboxAuthError(f"Xbox Live device token request failed: {response.status_code} {response.text}")

    return response.json()["Token"]


async def get_title_token(
    client: httpx.AsyncClient, signing_key: XboxSigningKey, msa_access_token: str, device_token: str
) -> str:
    payload = {
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
        "Properties": {
            "AuthMethod": "RPS",
            "DeviceToken": device_token,
            "RpsTicket": f"t={msa_access_token}",
            "SiteName": "user.auth.xboxlive.com",
            "ProofKey": signing_key.jwk(),
        },
    }
    body = _json_bytes(payload)
    signature = signing_key.sign_request(TITLE_AUTH_URL, body)

    response = await client.post(
        TITLE_AUTH_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "1",
            "Signature": _b64_std(signature),
        },
    )
    if response.status_code != 200:
        raise XboxAuthError(f"Xbox Live title token request failed: {response.status_code} {response.text}")

    return response.json()["Token"]


async def get_xsts_token(
    client: httpx.AsyncClient,
    signing_key: XboxSigningKey,
    user_token: str,
    device_token: str,
    title_token: str,
    relying_party: str = "rp://api.minecraftservices.com/",
) -> XstsToken:
    payload = {
        "RelyingParty": relying_party,
        "TokenType": "JWT",
        "Properties": {
            "UserTokens": [user_token],
            "DeviceToken": device_token,
            "TitleToken": title_token,
            "SandboxId": "RETAIL",
            "ProofKey": signing_key.jwk(),
        },
    }
    body = _json_bytes(payload)
    signature = signing_key.sign_request(XSTS_AUTHORIZE_URL, body)

    response = await client.post(
        XSTS_AUTHORIZE_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "1",
            "Signature": _b64_std(signature),
        },
    )
    payload = response.json()

    if response.status_code != 200:
        xbox_error = payload.get("XErr")
        raise XboxAuthError(_describe_xsts_error(xbox_error) or f"XSTS authorization failed: {payload}")

    claims = payload["DisplayClaims"]["xui"][0]
    return XstsToken(token=payload["Token"], user_hash=claims["uhs"], expires_on=payload["NotAfter"])


_XSTS_ERRORS = {
    2148916233: "This Microsoft account has no Xbox profile. Create one at https://signup.live.com/signup",
    2148916235: "Xbox Live is not available in this account's region.",
    2148916236: "This account needs adult verification on the Xbox website.",
    2148916237: "This account has reached its playtime limit.",
    2148916238: "This account is a child account and must be added to a Microsoft family group.",
}


def _describe_xsts_error(xerr: int | None) -> str | None:
    if xerr is None:
        return None
    return _XSTS_ERRORS.get(xerr, f"Xbox Live authentication failed (XErr={xerr})")


def _json_bytes(payload: dict) -> bytes:
    import json

    return json.dumps(payload).encode("utf-8")


def _b64_std(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
