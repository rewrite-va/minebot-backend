"""compute_server_hash reference values were cross-checked against real
`java.math.BigInteger(sha1_digest).toString(16)` output (compiled and run
locally with `javac`/`java`), not recalled from memory -- an earlier attempt
using remembered "known" values turned out to be wrong, so these are
regenerated from the actual JVM semantics this algorithm has to match.
"""

import hashlib

from cryptography.hazmat.primitives.asymmetric import rsa

from minebot.auth.encryption import (
    compute_server_hash,
    encrypt_with_server_key,
    generate_shared_secret,
    make_cfb8_cipher,
)


def test_compute_server_hash_matches_java_biginteger_reference():
    # java.math.BigInteger(MessageDigest.getInstance("SHA-1").digest(s.getBytes("ISO_8859_1"))).toString(16)
    cases = {
        "Notch": "4ed1f46bbe04bc756bcb17c0c7ce3e4632f06a48",
        "jeb_": "-7c9d5b0044c130109a5d7b5fb5c317c02b4e28c1",
        "simon": "88e16a1019277b15d58faf0541e11910eb756f6",
    }
    for server_id, expected in cases.items():
        # compute_server_hash hashes serverId + secret + public_key_der;
        # passing empty bytes for the latter two reduces it to a plain
        # sha1(server_id) case matching the reference computation above.
        assert compute_server_hash(server_id, b"", b"") == expected


def test_generate_shared_secret_is_16_bytes():
    secret = generate_shared_secret()
    assert len(secret) == 16
    assert generate_shared_secret() != secret


def test_rsa_encrypt_roundtrip():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public_key = private_key.public_key()

    secret = b"0123456789abcdef"
    encrypted = encrypt_with_server_key(public_key, secret)

    from cryptography.hazmat.primitives.asymmetric import padding

    decrypted = private_key.decrypt(encrypted, padding.PKCS1v15())
    assert decrypted == secret


def test_cfb8_cipher_roundtrip():
    secret = generate_shared_secret()
    enc_encryptor, enc_decryptor = make_cfb8_cipher(secret)
    dec_encryptor, dec_decryptor = make_cfb8_cipher(secret)

    plaintext = b"hello minecraft protocol, this is a longer test message"
    ciphertext = enc_encryptor.update(plaintext)
    roundtripped = dec_decryptor.update(ciphertext)

    assert roundtripped == plaintext
    assert ciphertext != plaintext
