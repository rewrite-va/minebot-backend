"""Online-mode login encryption: the shared-secret exchange triggered by
ClientboundHelloPacket, and Mojang's session-join call that must happen in
between generating the secret and replying with ServerboundKeyPacket.

Algorithm specifics read straight off the decompiled net.minecraft.util.Crypt
(see FINDINGS.md): AES-128 shared secret, RSA (PKCS#1 v1.5 padding, Java's
default for a bare "RSA" Cipher.getInstance) to wrap the secret+challenge for
transport, and AES/CFB8/NoPadding (IV = the secret itself) for the connection
once both sides have the secret. The "server hash" Mojang's session server
expects is not just a plain hex SHA-1 digest -- it's run through a
Minecraft-specific signed-BigInteger hex encoding first; this is
undocumented in the vanilla source itself (computed server-side against the
same raw digest) but is a stable, widely-known algorithm every MC client
implements identically.
"""

from __future__ import annotations

import hashlib

import httpx
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.decrepit.ciphers.modes import CFB8
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.serialization import load_der_public_key

JOIN_SERVER_URL = "https://sessionserver.mojang.com/session/minecraft/join"


class SessionJoinError(Exception):
    pass


def generate_shared_secret() -> bytes:
    import os

    return os.urandom(16)  # AES-128, per Crypt.generateSecretKey()


def load_server_public_key(der_bytes: bytes) -> RSAPublicKey:
    key = load_der_public_key(der_bytes)
    if not isinstance(key, RSAPublicKey):
        raise ValueError("server sent a non-RSA public key")
    return key


def encrypt_with_server_key(public_key: RSAPublicKey, data: bytes) -> bytes:
    # Java's Cipher.getInstance("RSA") defaults to RSA/ECB/PKCS1Padding.
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def compute_server_hash(server_id: str, shared_secret: bytes, server_public_key_der: bytes) -> str:
    """Mojang's `hexdigest` server hash: SHA-1 over serverId (as raw ASCII/
    Latin-1 bytes, matching Crypt.digestData's "ISO_8859_1" encoding) +
    shared secret + the server's DER-encoded public key, then formatted as a
    signed hex string (two's-complement negative numbers get a '-' prefix
    with the magnitude negated, rather than Python/Java's usual unsigned hex).
    """
    digest = hashlib.sha1()
    digest.update(server_id.encode("latin-1"))
    digest.update(shared_secret)
    digest.update(server_public_key_der)
    return _minecraft_signed_hex_digest(digest.digest())


def _minecraft_signed_hex_digest(digest_bytes: bytes) -> str:
    value = int.from_bytes(digest_bytes, byteorder="big", signed=True)
    if value < 0:
        return "-" + format(-value, "x")
    return format(value, "x")


async def join_server(client: httpx.AsyncClient, access_token: str, profile_id: str, server_hash: str) -> None:
    selected_profile = profile_id.replace("-", "")
    response = await client.post(
        JOIN_SERVER_URL,
        json={
            "accessToken": access_token,
            "selectedProfile": selected_profile,
            "serverId": server_hash,
        },
    )
    if response.status_code != 204:
        raise SessionJoinError(f"session join failed: {response.status_code} {response.text}")


def make_cfb8_cipher(shared_secret: bytes):
    """Returns (encryptor, decryptor) matching Connection.getCipher's
    AES/CFB8/NoPadding with IV = the shared secret. cryptography's CFB mode
    is generic (not fixed to 8-bit feedback like Java's "CFB8" transformation
    name implies) -- but its `modes.CFB` *is* 8-bit/full-feedback-per-byte
    behavior compatible with Java's CFB8, both being byte-at-a-time CFB with
    no internal buffering across bytes, unlike CFB128.
    """
    cipher = Cipher(algorithms.AES(shared_secret), CFB8(shared_secret))
    return cipher.encryptor(), cipher.decryptor()
