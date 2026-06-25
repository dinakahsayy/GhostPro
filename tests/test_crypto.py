import os

import pytest
from cryptography.fernet import InvalidToken
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, User
from app.utils.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    assert decrypt(encrypt("hunter2")) == "hunter2"


def test_ciphertext_differs_from_plaintext():
    plaintext = "ya29.A0ARrdaM-secret-token"
    assert encrypt(plaintext) != plaintext


def test_encryption_is_non_deterministic():
    # Fernet embeds a random IV + timestamp, so the same input yields different
    # ciphertext each time — both must still decrypt back to the original.
    a, b = encrypt("same-token"), encrypt("same-token")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same-token"


def test_none_passes_through():
    assert encrypt(None) is None
    assert decrypt(None) is None


def test_decrypt_garbage_raises():
    with pytest.raises(InvalidToken):
        decrypt("not-a-valid-fernet-token")


def test_decrypt_with_wrong_key_raises(monkeypatch):
    token = encrypt("secret")
    monkeypatch.setenv("FERNET_KEY", "")
    monkeypatch.setenv("SECRET_KEY", "a-completely-different-secret")
    with pytest.raises(InvalidToken):
        decrypt(token)


def test_encrypted_column_stores_ciphertext():
    """Through the ORM the value is plaintext; on disk it is ciphertext."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True)

    raw_token = "linkedin-access-token-xyz"
    with TestSession() as s:
        s.add(User(email="enc@example.com", linkedin_access_token=raw_token))
        s.commit()

    # ORM round trip returns plaintext.
    with TestSession() as s:
        user = s.query(User).filter_by(email="enc@example.com").one()
        assert user.linkedin_access_token == raw_token

    # The raw column value is encrypted, not the plaintext token.
    with engine.connect() as conn:
        from sqlalchemy import text
        stored = conn.execute(
            text("SELECT linkedin_access_token FROM users WHERE email = :e"),
            {"e": "enc@example.com"},
        ).scalar_one()
    assert stored != raw_token
    assert decrypt(stored) == raw_token
