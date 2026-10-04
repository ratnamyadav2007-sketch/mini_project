import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.security.field_encryption import (
    EncryptedField,
    decrypt_field,
    encrypt_field,
)
from app.security.key_rotation import rotate_wrapped_data_keys


def _user(session: Session, email: str) -> User:
    user = User(email=email, password_hash="unused", display_name=email)
    session.add(user)
    session.flush()
    return user


def test_aes_gcm_field_encryption_is_user_and_field_bound(db_session: Session) -> None:
    wrapping_key = bytes(range(32))
    owner = _user(db_session, "crypto-owner@example.com")
    other = _user(db_session, "crypto-other@example.com")
    encrypted = encrypt_field(
        db_session,
        owner,
        "health_records.notes",
        "private clinical note",
        wrapping_key=wrapping_key,
        key_version=1,
    )

    assert encrypted.ciphertext != b"private clinical note"
    assert (
        decrypt_field(
            db_session,
            owner,
            "health_records.notes",
            encrypted,
            wrapping_key=wrapping_key,
            key_version=1,
        )
        == b"private clinical note"
    )

    for wrong_user, wrong_field in (
        (other, "health_records.notes"),
        (owner, "health_records.value"),
    ):
        try:
            decrypt_field(
                db_session,
                wrong_user,
                wrong_field,
                encrypted,
                wrapping_key=wrapping_key,
                key_version=1,
            )
        except ValueError as error:
            assert "unwrap" in str(error) or "authentication failed" in str(error)
        else:
            raise AssertionError("Ciphertext must be bound to its owner and field")


def test_key_rotation_rewraps_user_key_without_changing_ciphertext(
    db_session: Session,
) -> None:
    old_master = bytes(range(32))
    new_master = bytes(range(32, 64))
    user = _user(db_session, "rotation@example.com")
    encrypted = encrypt_field(
        db_session,
        user,
        "health_records.notes",
        "unchanged ciphertext",
        wrapping_key=old_master,
        key_version=1,
    )
    data_ciphertext = encrypted.ciphertext
    data_nonce = encrypted.nonce
    wrapped_key_before = user.wrapped_data_key

    rotated = rotate_wrapped_data_keys(
        db_session,
        old_master_key=old_master,
        new_master_key=new_master,
        new_key_version=2,
    )

    assert rotated == 1
    assert user.wrapped_data_key != wrapped_key_before
    assert user.data_key_version == 2
    assert (
        decrypt_field(
            db_session,
            user,
            "health_records.notes",
            EncryptedField(nonce=data_nonce, ciphertext=data_ciphertext),
            wrapping_key=new_master,
            key_version=2,
        )
        == b"unchanged ciphertext"
    )


def test_key_rotation_fails_closed_on_wrong_old_key(db_session: Session) -> None:
    user = _user(db_session, "rotation-failure@example.com")
    encrypt_field(
        db_session,
        user,
        "health_records.notes",
        "secret",
        wrapping_key=bytes(range(32)),
        key_version=1,
    )
    original_wrapped_key = user.wrapped_data_key
    db_session.commit()

    with pytest.raises(InvalidTag):
        rotate_wrapped_data_keys(
            db_session,
            old_master_key=bytes(range(1, 33)),
            new_master_key=bytes(range(32, 64)),
            new_key_version=2,
        )
    persisted = db_session.scalar(select(User).where(User.id == user.id))
    assert persisted is not None
    assert persisted.data_key_version == 1
    assert persisted.wrapped_data_key == original_wrapped_key
