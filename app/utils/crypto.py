# app/utils/crypto.py
# Fernet symmetric encryption for data at rest (LinkedIn OAuth tokens, §11.1).
#
# Key resolution order:
#   1. FERNET_KEY env var  — a urlsafe-base64 32-byte key (Fernet.generate_key())
#   2. SECRET_KEY env var  — deterministically derived into a Fernet key via SHA-256
#
# IMPORTANT: the key must remain stable. Rotating FERNET_KEY (or SECRET_KEY when
# no FERNET_KEY is set) makes previously stored ciphertext undecryptable — affected
# users would need to re-connect LinkedIn. Set a dedicated FERNET_KEY in production.

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["encrypt", "decrypt", "EncryptedString", "InvalidToken"]


def _derive_key_from_secret(secret_key: str) -> bytes:
    """Turn an arbitrary SECRET_KEY string into a valid 32-byte Fernet key."""
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    """Build a Fernet instance from the configured key. Cheap; called per op."""
    key = os.getenv("FERNET_KEY")
    if key:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)

    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "No encryption key configured: set FERNET_KEY or SECRET_KEY"
        )
    return Fernet(_derive_key_from_secret(secret))


def encrypt(plaintext):
    """Encrypt a string. Returns None for None input."""
    if plaintext is None:
        return None
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt(ciphertext):
    """Decrypt a string produced by encrypt(). Returns None for None input.

    Raises cryptography.fernet.InvalidToken if the ciphertext is corrupt or was
    encrypted under a different key.
    """
    if ciphertext is None:
        return None
    value = ciphertext.encode("utf-8") if isinstance(ciphertext, str) else ciphertext
    return _get_fernet().decrypt(value).decode("utf-8")


# Imported here (not at module top) to keep the pure-crypto helpers above free of
# any SQLAlchemy dependency for callers that only need encrypt/decrypt.
from sqlalchemy import Text  # noqa: E402
from sqlalchemy.types import TypeDecorator  # noqa: E402


class EncryptedString(TypeDecorator):
    """A Text column that transparently Fernet-encrypts on write and decrypts on
    read, so application/ORM code only ever sees plaintext while the database
    stores ciphertext.

    The underlying column type is TEXT, so swapping a plain Text column to
    EncryptedString needs no DDL migration.

    If a stored value cannot be decrypted (key changed or corruption), reads
    return None rather than raising, so loading a row never hard-fails on a
    single bad token — callers should treat a None token as "re-auth needed".
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return decrypt(value)
        except InvalidToken:
            return None
