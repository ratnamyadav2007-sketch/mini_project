import base64
import binascii
import secrets
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import User


@dataclass(frozen=True)
class EncryptedField:
    nonce: bytes
    ciphertext: bytes


def _key_bytes(value: str | bytes, *, setting_name: str) -> bytes:
    if isinstance(value, bytes):
        key = value
    else:
        try:
            key = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exception:
            raise ValueError(f"{setting_name} must be a base64-encoded 32-byte key") from exception
    if len(key) != 32:
        raise ValueError(f"{setting_name} must decode to exactly 32 bytes")
    return key


def master_key(settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    if settings.master_encryption_key is None:
        raise RuntimeError("MASTER_ENCRYPTION_KEY must be configured for field encryption")
    return _key_bytes(
        settings.master_encryption_key.get_secret_value(),
        setting_name="MASTER_ENCRYPTION_KEY",
    )


def _wrap_aad(user_id: UUID, key_version: int) -> bytes:
    return f"user-data-key:{user_id}:{key_version}".encode("ascii")


def _field_aad(user_id: UUID, field_name: str) -> bytes:
    if not field_name or len(field_name) > 128:
        raise ValueError("field_name must contain between 1 and 128 characters")
    return f"user-field:{user_id}:{field_name}".encode()


def create_user_data_key(
    session: Session,
    user: User,
    *,
    wrapping_key: bytes | None = None,
    key_version: int | None = None,
) -> bytes:
    if user.wrapped_data_key is not None or user.data_key_nonce is not None:
        if (
            user.wrapped_data_key is None
            or user.data_key_nonce is None
            or user.data_key_version is None
        ):
            raise ValueError("User data key metadata is incomplete")
        return unwrap_user_data_key(
            user,
            wrapping_key=wrapping_key,
            key_version=key_version,
        )

    settings = get_settings()
    master = wrapping_key if wrapping_key is not None else master_key(settings)
    version = settings.master_encryption_key_version if key_version is None else key_version
    if version < 1:
        raise ValueError("key_version must be positive")
    data_key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    wrapped = AESGCM(master).encrypt(nonce, data_key, _wrap_aad(user.id, version))
    user.wrapped_data_key = wrapped
    user.data_key_nonce = nonce
    user.data_key_version = version
    session.flush()
    return data_key


def unwrap_user_data_key(
    user: User,
    *,
    wrapping_key: bytes | None = None,
    key_version: int | None = None,
) -> bytes:
    if (
        user.wrapped_data_key is None
        or user.data_key_nonce is None
        or user.data_key_version is None
    ):
        raise ValueError("User does not have a wrapped data key")
    master = wrapping_key if wrapping_key is not None else master_key()
    version = user.data_key_version if key_version is None else key_version
    if version != user.data_key_version:
        raise ValueError("Provided master-key version does not match the user's wrapped key")
    try:
        return AESGCM(master).decrypt(
            user.data_key_nonce,
            user.wrapped_data_key,
            _wrap_aad(user.id, user.data_key_version),
        )
    except InvalidTag as exception:
        raise ValueError(
            "Unable to unwrap user data key with the supplied master key"
        ) from exception


def encrypt_field(
    session: Session,
    user: User,
    field_name: str,
    plaintext: str | bytes,
    *,
    wrapping_key: bytes | None = None,
    key_version: int | None = None,
) -> EncryptedField:
    data_key = create_user_data_key(
        session,
        user,
        wrapping_key=wrapping_key,
        key_version=key_version,
    )
    value = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(data_key).encrypt(nonce, value, _field_aad(user.id, field_name))
    return EncryptedField(nonce=nonce, ciphertext=ciphertext)


def decrypt_field(
    session: Session,
    user: User,
    field_name: str,
    encrypted: EncryptedField,
    *,
    wrapping_key: bytes | None = None,
    key_version: int | None = None,
) -> bytes:
    data_key = create_user_data_key(
        session,
        user,
        wrapping_key=wrapping_key,
        key_version=key_version,
    )
    try:
        return AESGCM(data_key).decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            _field_aad(user.id, field_name),
        )
    except InvalidTag as exception:
        raise ValueError("Encrypted field authentication failed") from exception
