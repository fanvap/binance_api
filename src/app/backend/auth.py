"""Local credential verification for the desktop login screen."""

import hashlib
import hmac
import secrets
from typing import Dict, Tuple


_ITERATIONS = 600_000
_DUMMY_SALT = bytes.fromhex("0d2f3c7f88a3dd12bca9a3055e4106bc")
_DUMMY_DIGEST = "4e1f8d7d0f639ec08e9b38a0d4c9f3c0d80c99c930268c390095defd0ea6a123"
_USERS: Dict[str, Tuple[str, str]] = {
    "phapnv": (
        "d4148ff0bd644361152965c2f20fbbe6",
        "84f614107426b1a1a2a27a35c538b7b016d61a054467608dfcc91a996b95f04f",
    ),
}


def create_password_record(password: str) -> Tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    ).hex()
    return salt.hex(), digest


def verify_credentials(username: str, password: str) -> bool:
    normalized_username = username.strip()
    credential = _USERS.get(normalized_username)
    if credential is None:
        salt = _DUMMY_SALT
        expected_digest = _DUMMY_DIGEST
    else:
        salt = bytes.fromhex(credential[0])
        expected_digest = credential[1]

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    ).hex()
    return credential is not None and hmac.compare_digest(actual_digest, expected_digest)
