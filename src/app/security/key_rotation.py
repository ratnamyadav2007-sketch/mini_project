import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.security.field_encryption import _key_bytes, _wrap_aad


def rotate_wrapped_data_keys(
    session: Session,
    *,
    old_master_key: bytes,
    new_master_key: bytes,
    new_key_version: int,
) -> int:
    users = session.scalars(
        select(User).where(User.wrapped_data_key.is_not(None)).order_by(User.id).with_for_update()
    )
    rotated = 0
    for user in users:
        if user.data_key_nonce is None or user.data_key_version is None:
            raise ValueError(f"User {user.id} has incomplete wrapped data-key metadata")
        old_version = user.data_key_version
        if new_key_version <= old_version:
            raise ValueError("New master-key version must exceed every existing user key version")
        data_key = AESGCM(old_master_key).decrypt(
            user.data_key_nonce,
            user.wrapped_data_key,
            _wrap_aad(user.id, old_version),
        )
        nonce = secrets.token_bytes(12)
        wrapped = AESGCM(new_master_key).encrypt(
            nonce,
            data_key,
            _wrap_aad(user.id, new_key_version),
        )
        user.data_key_nonce = nonce
        user.wrapped_data_key = wrapped
        user.data_key_version = new_key_version
        rotated += 1
    session.flush()
    return rotated


def main() -> None:
    settings = get_settings()
    if settings.old_master_encryption_key is None:
        raise RuntimeError("OLD_MASTER_ENCRYPTION_KEY must be set for key rotation")
    if settings.new_master_encryption_key is None:
        raise RuntimeError("NEW_MASTER_ENCRYPTION_KEY must be set for key rotation")
    if settings.new_master_encryption_key_version is None:
        raise RuntimeError("NEW_MASTER_ENCRYPTION_KEY_VERSION must be set for key rotation")
    old_key = _key_bytes(
        settings.old_master_encryption_key.get_secret_value(),
        setting_name="OLD_MASTER_ENCRYPTION_KEY",
    )
    new_key = _key_bytes(
        settings.new_master_encryption_key.get_secret_value(),
        setting_name="NEW_MASTER_ENCRYPTION_KEY",
    )
    if old_key == new_key:
        raise ValueError("Old and new master encryption keys must differ")
    if settings.new_master_encryption_key_version <= settings.master_encryption_key_version:
        raise ValueError("New master-key version must exceed MASTER_ENCRYPTION_KEY_VERSION")
    with SessionLocal.begin() as session:
        rotated = rotate_wrapped_data_keys(
            session,
            old_master_key=old_key,
            new_master_key=new_key,
            new_key_version=settings.new_master_encryption_key_version,
        )
    print(f"Rotated {rotated} user data key(s).")


if __name__ == "__main__":
    main()
