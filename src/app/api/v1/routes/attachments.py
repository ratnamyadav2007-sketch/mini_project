import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.core.config import get_settings
from app.db.models import Attachment, HealthRecord, MemberProfile, User
from app.security.audit import append_audit_event
from app.security.dependencies import (
    CurrentAuthenticatedSession,
    DbSession,
    require_record_access,
)
from app.security.field_encryption import EncryptedField, decrypt_field, encrypt_field
from app.security.permissions import Action, Visibility, evaluate_access

router = APIRouter(tags=["attachments"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
FILE_SIGNATURES: tuple[tuple[str, bytes, str], ...] = (
    ("application/pdf", b"%PDF-", ".pdf"),
    ("image/png", b"\x89PNG\r\n\x1a\n", ".png"),
    ("image/jpeg", b"\xff\xd8\xff", ".jpg"),
)
OBJECT_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class AttachmentResponse(BaseModel):
    id: UUID
    profile_id: UUID
    record_id: UUID | None
    original_filename: str
    content_type: str
    file_size_bytes: int
    visibility: Visibility


CurrentAttachmentRead = Annotated[
    Attachment, Depends(require_record_access(Attachment, "attachments", Action.READ))
]
AttachmentUpload = Annotated[UploadFile, File()]


def _detect_content_type(content: bytes) -> tuple[str, str]:
    for content_type, signature, extension in FILE_SIGNATURES:
        if content.startswith(signature):
            return content_type, extension
    raise HTTPException(status_code=415, detail="Only valid PDF, JPEG, and PNG files are supported")


def _storage_root() -> Path:
    settings = get_settings()
    root = Path(settings.attachment_storage_path)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _object_path(object_key: str) -> Path:
    if not OBJECT_KEY_PATTERN.fullmatch(object_key):
        raise HTTPException(status_code=404, detail="Attachment not found")
    root = _storage_root()
    path = (root / object_key).resolve()
    if path.parent != root:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return path


def _key_owner(session: DbSession, profile_id: UUID, actor: User) -> User:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id is None:
        return actor
    owner = session.get(User, profile.user_id)
    if owner is None:
        raise RuntimeError("Profile encryption key owner is unavailable")
    return owner


def _safe_filename(original: str, extension: str) -> tuple[str, str]:
    leaf = Path(original.replace("\x00", "")).name
    stem = Path(leaf).stem.strip().strip(".")
    stem = "".join(
        character for character in stem if character.isprintable() and character not in '"/\\'
    )
    stem = stem[:120].strip()
    if not stem:
        stem = "attachment"
    filename = f"{stem}{extension}"
    ascii_stem = stem.encode("ascii", errors="ignore").decode("ascii")
    fallback = f"{ascii_stem or 'attachment'}{extension}".replace('"', "")
    return filename, fallback


def _response(
    attachment: Attachment,
    *,
    profile_id: UUID | None = None,
) -> AttachmentResponse:
    if profile_id is not None and attachment.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        visibility = Visibility(attachment.visibility)
    except ValueError as exception:
        raise RuntimeError("Attachment has an invalid visibility value") from exception
    return AttachmentResponse(
        id=attachment.id,
        profile_id=attachment.profile_id,
        record_id=attachment.record_id,
        original_filename=attachment.original_filename,
        content_type=attachment.content_type,
        file_size_bytes=attachment.file_size_bytes,
        visibility=visibility,
    )


@router.post(
    "/profiles/{profile_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    profile_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
    file: AttachmentUpload,
    record_id: Annotated[UUID | None, Form()] = None,
) -> AttachmentResponse:
    if record_id is not None:
        record = session.get(HealthRecord, record_id)
        if record is None or record.deleted_at is not None or record.profile_id != profile_id:
            raise HTTPException(status_code=404, detail="Health record not found")
    decision = evaluate_access(
        session,
        actor=authenticated.user,
        profile_id=profile_id,
        visibility=Visibility.PRIVATE,
        action=Action.WRITE,
        resource="attachments",
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile_id,
            action="attachment_upload_denied",
            entity_type="attachments",
            details={"reason": decision.reason},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")

    settings = get_settings()
    max_bytes = min(settings.attachment_max_size_bytes, MAX_UPLOAD_BYTES)
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Attachment exceeds the 10 MiB limit")
    content_type, extension = _detect_content_type(content)
    original_filename, _ = _safe_filename(file.filename or "", extension)
    object_key = uuid4().hex
    path = _object_path(object_key)
    key_owner = _key_owner(session, profile_id, authenticated.user)
    encrypted = encrypt_field(
        session,
        key_owner,
        f"attachments:{object_key}",
        content,
    )
    attachment = Attachment(
        profile_id=profile_id,
        record_id=record_id,
        uploaded_by_user_id=authenticated.user.id,
        encryption_key_owner_id=key_owner.id,
        object_key=object_key,
        content_type=content_type,
        file_size_bytes=len(content),
        original_filename=original_filename,
        nonce=encrypted.nonce,
        visibility=Visibility.PRIVATE.value,
    )
    session.add(attachment)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="attachment_uploaded",
        entity_type="attachments",
        entity_id=attachment.id,
        details={"content_type": content_type, "size": str(len(content))},
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as encrypted_file:
            encrypted_file.write(encrypted.ciphertext)
        session.commit()
    except Exception:
        session.rollback()
        if path.exists():
            path.unlink()
        raise
    session.refresh(attachment)
    return _response(attachment)


@router.get("/attachments/{record_id}", response_model=AttachmentResponse)
def get_attachment_metadata(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    attachment: CurrentAttachmentRead,
) -> AttachmentResponse:
    del authenticated, session
    response.headers["Cache-Control"] = "no-store"
    return _response(attachment)


@router.get("/attachments/{record_id}/download")
def download_attachment(
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    attachment: CurrentAttachmentRead,
) -> Response:
    if attachment.nonce is None or attachment.encryption_key_owner_id is None:
        raise RuntimeError("Attachment encryption metadata is incomplete")
    owner = session.get(User, attachment.encryption_key_owner_id)
    if owner is None:
        raise RuntimeError("Attachment encryption key owner is unavailable")
    path = _object_path(attachment.object_key)
    try:
        ciphertext = path.read_bytes()
    except FileNotFoundError as exception:
        raise HTTPException(status_code=404, detail="Attachment file not found") from exception
    try:
        plaintext = decrypt_field(
            session,
            owner,
            f"attachments:{attachment.object_key}",
            EncryptedField(nonce=attachment.nonce, ciphertext=ciphertext),
        )
    except (InvalidTag, ValueError) as exception:
        raise RuntimeError("Attachment integrity verification failed") from exception

    extension = next(
        (content_type, extension)
        for content_type, signature, extension in FILE_SIGNATURES
        if attachment.content_type == content_type
    )
    filename, fallback = _safe_filename(attachment.original_filename, extension)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=attachment.profile_id,
        action="attachment_downloaded",
        entity_type="attachments",
        entity_id=attachment.id,
    )
    session.commit()
    return Response(
        content=plaintext,
        media_type=attachment.content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )
