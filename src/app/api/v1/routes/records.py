import base64
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import and_, or_, select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.db.models import Attachment, HealthRecord, MemberProfile, RecordType, ShareGrant, User
from app.security.audit import append_audit_event
from app.security.dependencies import (
    CurrentAuthenticatedSession,
    DbSession,
    require_health_record_access,
)
from app.security.field_encryption import EncryptedField, decrypt_field, encrypt_field
from app.security.permissions import Action, Role, Visibility, evaluate_access

router = APIRouter(tags=["health records"])
RECORD_TYPE_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "code": "condition",
        "display_name": "Condition",
        "icon": "heart",
        "fields": [
            {"name": "name", "label": "Condition", "type": "text", "required": True},
            {
                "name": "status",
                "label": "Status",
                "type": "select",
                "options": ["Current", "Monitoring", "Resolved"],
            },
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "medication",
        "display_name": "Medication",
        "icon": "pill",
        "fields": [
            {"name": "name", "label": "Medication name", "type": "text", "required": True},
            {"name": "dosage", "label": "Dose", "type": "text"},
            {"name": "frequency", "label": "Frequency", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "allergy",
        "display_name": "Allergy",
        "icon": "alert-triangle",
        "fields": [
            {"name": "name", "label": "Allergen", "type": "text", "required": True},
            {"name": "reaction", "label": "Reaction", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "vaccination",
        "display_name": "Vaccination",
        "icon": "shield",
        "fields": [
            {"name": "name", "label": "Vaccine", "type": "text", "required": True},
            {"name": "provider", "label": "Provider", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "lab",
        "display_name": "Laboratory",
        "icon": "activity",
        "fields": [
            {"name": "name", "label": "Test", "type": "text", "required": True},
            {"name": "value", "label": "Result", "type": "text"},
            {"name": "unit", "label": "Unit", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "vital",
        "display_name": "Vital",
        "icon": "activity",
        "fields": [
            {"name": "name", "label": "Measurement", "type": "text", "required": True},
            {"name": "value", "label": "Value", "type": "text", "required": True},
            {"name": "unit", "label": "Unit", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "surgery",
        "display_name": "Surgery",
        "icon": "activity",
        "fields": [
            {"name": "name", "label": "Procedure", "type": "text", "required": True},
            {"name": "provider", "label": "Provider", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "visit_note",
        "display_name": "Visit note",
        "icon": "clipboard",
        "fields": [
            {"name": "summary", "label": "Visit summary", "type": "textarea", "required": True},
            {"name": "provider", "label": "Provider", "type": "text"},
            {"name": "location", "label": "Location", "type": "text"},
        ],
    },
    {
        "code": "blood_group",
        "display_name": "Blood group",
        "icon": "heart",
        "fields": [
            {
                "name": "group",
                "label": "Blood group",
                "type": "select",
                "required": True,
                "options": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
            },
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "systolic_blood_pressure",
        "display_name": "Systolic blood pressure",
        "icon": "activity",
        "fields": [
            {
                "name": "value",
                "label": "Systolic value",
                "type": "number",
                "required": True,
                "unit": "mmHg",
            },
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "body_temperature",
        "display_name": "Body temperature",
        "icon": "activity",
        "fields": [
            {
                "name": "value",
                "label": "Temperature",
                "type": "number",
                "required": True,
                "unit": "°C",
            },
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
    {
        "code": "blood_glucose",
        "display_name": "Blood glucose",
        "icon": "activity",
        "fields": [
            {
                "name": "value",
                "label": "Blood glucose",
                "type": "number",
                "required": True,
                "unit": "mg/dL",
            },
            {"name": "notes", "label": "Notes", "type": "textarea"},
        ],
    },
)
RECORD_TYPES = frozenset(schema["code"] for schema in RECORD_TYPE_SCHEMAS)
SELECTABLE_GRANT_ROLES = frozenset(
    {Role.ADULT, Role.GUARDIAN, Role.DEPENDENT, Role.VIEWER, Role.EMERGENCY_CONTACT}
)


class RecordCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    recorded_at: datetime
    data: dict[str, Any]
    visibility: Visibility = Visibility.PRIVATE

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        return value.strip().casefold()


class RecordUpdate(BaseModel):
    type: str | None = Field(default=None, min_length=1, max_length=64)
    recorded_at: datetime | None = None
    data: dict[str, Any] | None = None
    visibility: Visibility | None = None

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("recorded_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        return value.strip().casefold() if value is not None else None

    @model_validator(mode="after")
    def require_update(self) -> "RecordUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class AttachmentSummary(BaseModel):
    id: UUID
    original_filename: str
    content_type: str
    file_size_bytes: int


class RecordResponse(BaseModel):
    id: UUID
    profile_id: UUID
    type: str
    recorded_at: datetime
    visibility: Visibility
    data: dict[str, Any]
    attachments: list[AttachmentSummary] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    items: list[RecordResponse]
    offset: int
    limit: int
    has_more: bool
    next_cursor: str | None


class ShareCreate(BaseModel):
    email: EmailStr
    role: Role = Role.VIEWER
    permissions: list[Action] = Field(default_factory=lambda: [Action.READ], min_length=1)
    expires_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("expires_at")
    @classmethod
    def require_future_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        value = value.astimezone(UTC)
        if value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[Action]) -> list[Action]:
        if len(set(value)) != len(value):
            raise ValueError("permissions must be unique")
        return value

    @model_validator(mode="after")
    def constrain_role_actions(self) -> "ShareCreate":
        if self.role not in SELECTABLE_GRANT_ROLES:
            raise ValueError("This role cannot be delegated")
        if self.role in {Role.VIEWER, Role.EMERGENCY_CONTACT} and any(
            permission != Action.READ for permission in self.permissions
        ):
            raise ValueError("Viewer and Emergency Contact grants are read-only")
        if self.role == Role.EMERGENCY_CONTACT and self.permissions != [Action.READ]:
            raise ValueError("Emergency Contact grants are read-only")
        return self


class ShareResponse(BaseModel):
    id: UUID
    record_id: UUID
    granted_to_user_id: UUID
    access_role: Role
    permissions: list[Action]
    expires_at: datetime | None


CurrentRecordRead = Annotated[HealthRecord, Depends(require_health_record_access(Action.READ))]
CurrentRecordWrite = Annotated[HealthRecord, Depends(require_health_record_access(Action.WRITE))]
CurrentRecordDelete = Annotated[HealthRecord, Depends(require_health_record_access(Action.DELETE))]


def _record_key_owner(session: DbSession, profile_id: UUID, actor: User) -> User:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id is None:
        return actor
    owner = session.get(User, profile.user_id)
    if owner is None:
        raise RuntimeError("Profile encryption key owner is unavailable")
    return owner


def _record_payload(record: HealthRecord, key_owner: User, session: DbSession) -> dict[str, Any]:
    if record.encrypted_payload is not None:
        if record.payload_nonce is None:
            raise RuntimeError("Encrypted health record is missing its nonce")
        if record.payload_key_owner_id is None:
            raise RuntimeError("Encrypted health record is missing its key owner")
        actual_key_owner = session.get(User, record.payload_key_owner_id)
        if actual_key_owner is None:
            raise RuntimeError("Encrypted health record key owner is unavailable")
        plaintext = decrypt_field(
            session,
            actual_key_owner,
            f"health_records:{record.id}",
            EncryptedField(record.payload_nonce, record.encrypted_payload),
        )
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise RuntimeError("Encrypted health record payload has an invalid shape")
        return value
    if key_owner.id != record.payload_key_owner_id and record.payload_key_owner_id is not None:
        raise RuntimeError("Health record key owner mismatch")
    legacy: dict[str, Any] = {}
    if record.value is not None:
        legacy["value"] = str(record.value)
    if record.unit is not None:
        legacy["unit"] = record.unit
    if record.notes is not None:
        legacy["notes"] = record.notes
    if record.source is not None:
        legacy["source"] = record.source
    return legacy


def _record_response(
    record: HealthRecord,
    actor: User,
    session: DbSession,
) -> RecordResponse:
    key_owner = _record_key_owner(session, record.profile_id, actor)
    if record.payload_key_owner_id is not None:
        key_owner = session.get(User, record.payload_key_owner_id)
        if key_owner is None:
            raise RuntimeError("Health record key owner is unavailable")
    attachments = session.scalars(
        select(Attachment).where(
            Attachment.record_id == record.id,
            Attachment.deleted_at.is_(None),
        )
    )
    visible_attachments = [
        AttachmentSummary(
            id=attachment.id,
            original_filename=attachment.original_filename,
            content_type=attachment.content_type,
            file_size_bytes=attachment.file_size_bytes,
        )
        for attachment in attachments
        if evaluate_access(
            session,
            actor=actor,
            profile_id=attachment.profile_id,
            visibility=attachment.visibility,
            action=Action.READ,
            resource="attachments",
        ).allowed
    ]
    return RecordResponse(
        id=record.id,
        profile_id=record.profile_id,
        type=record.type,
        recorded_at=record.recorded_at,
        visibility=Visibility(record.visibility),
        data=_record_payload(record, key_owner, session),
        attachments=visible_attachments,
    )


def _encode_timeline_cursor(record: HealthRecord) -> str:
    recorded_at = record.recorded_at
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    else:
        recorded_at = recorded_at.astimezone(UTC)
    cursor = json.dumps(
        {"recorded_at": recorded_at.isoformat(), "id": str(record.id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(cursor).decode().rstrip("=")


def _decode_timeline_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded).decode())
        recorded_at = datetime.fromisoformat(values["recorded_at"])
        record_id = UUID(values["id"])
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("Cursor timestamp must be timezone-aware")
        return recorded_at.astimezone(UTC), record_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exception:
        raise HTTPException(status_code=422, detail="Invalid timeline cursor") from exception


@router.get("/meta/record-types")
def get_record_type_schemas() -> list[dict[str, Any]]:
    return list(RECORD_TYPE_SCHEMAS)


def _encrypt_payload(
    session: DbSession,
    key_owner: User,
    record_id: UUID,
    data: dict[str, Any],
) -> EncryptedField:
    try:
        serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exception:
        raise HTTPException(
            status_code=422, detail="Record data must be JSON-compatible"
        ) from exception
    return encrypt_field(session, key_owner, f"health_records:{record_id}", serialized)


@router.post(
    "/profiles/{profile_id}/records",
    response_model=RecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_record(
    profile_id: UUID,
    body: RecordCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> RecordResponse:
    if (
        body.type not in RECORD_TYPES
        or session.scalar(select(RecordType.id).where(RecordType.code == body.type)) is None
    ):
        raise HTTPException(status_code=422, detail="Unsupported health record type")
    decision = evaluate_access(
        session,
        actor=authenticated.user,
        profile_id=profile_id,
        visibility=body.visibility,
        action=Action.WRITE,
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile_id,
            action="record_create_denied",
            entity_type="health_records",
            details={"reason": decision.reason, "type": body.type},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")

    key_owner = _record_key_owner(session, profile_id, authenticated.user)
    record = HealthRecord(
        profile_id=profile_id,
        type=body.type,
        recorded_at=body.recorded_at,
        visibility=body.visibility.value,
        value=None,
        unit=None,
        notes=None,
        source=None,
    )
    session.add(record)
    session.flush()
    encrypted = _encrypt_payload(session, key_owner, record.id, body.data)
    record.payload_nonce = encrypted.nonce
    record.encrypted_payload = encrypted.ciphertext
    record.payload_key_owner_id = key_owner.id
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="record_created",
        entity_type="health_records",
        entity_id=record.id,
        details={"type": record.type, "visibility": record.visibility},
    )
    session.commit()
    session.refresh(record)
    return _record_response(record, authenticated.user, session)


@router.get("/profiles/{profile_id}/timeline", response_model=TimelineResponse)
def get_timeline(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    record_type: str | None = Query(default=None, alias="type", max_length=64),
    start_year: int | None = Query(default=None, ge=1800, le=2200),
    end_year: int | None = Query(default=None, ge=1800, le=2200),
    search: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=512),
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=50, ge=1, le=100),
) -> TimelineResponse:
    if start_year is not None and end_year is not None and start_year > end_year:
        raise HTTPException(status_code=422, detail="start_year must not exceed end_year")
    if cursor is not None and offset:
        raise HTTPException(status_code=422, detail="cursor and offset cannot be used together")
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if record_type is not None and record_type.casefold() not in RECORD_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported health record type")

    statement = select(HealthRecord).where(
        HealthRecord.profile_id == profile_id,
        HealthRecord.deleted_at.is_(None),
    )
    if record_type is not None:
        statement = statement.where(HealthRecord.type == record_type.casefold())
    if start_year is not None:
        statement = statement.where(
            HealthRecord.recorded_at >= datetime(start_year, 1, 1, tzinfo=UTC)
        )
    if end_year is not None:
        statement = statement.where(
            HealthRecord.recorded_at < datetime(end_year + 1, 1, 1, tzinfo=UTC)
        )
    if cursor is not None:
        cursor_time, cursor_id = _decode_timeline_cursor(cursor)
        statement = statement.where(
            or_(
                HealthRecord.recorded_at < cursor_time,
                and_(
                    HealthRecord.recorded_at == cursor_time,
                    HealthRecord.id < cursor_id,
                ),
            )
        )
    statement = statement.order_by(HealthRecord.recorded_at.desc(), HealthRecord.id.desc())

    query = (search or "").strip().casefold()
    matched: list[HealthRecord] = []
    skipped = 0
    for record in session.scalars(statement):
        decision = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=profile_id,
            visibility=record.visibility,
            action=Action.READ,
            record_id=record.id,
            resource="health_records",
        )
        if not decision.allowed:
            continue
        payload = _record_payload(
            record,
            _record_key_owner(session, profile_id, authenticated.user),
            session,
        )
        searchable = (
            f"{record.type} {json.dumps(payload, ensure_ascii=False, default=str)}".casefold()
        )
        if query and query not in searchable:
            continue
        if cursor is None and skipped < offset:
            skipped += 1
            continue
        matched.append(record)
        if len(matched) > limit:
            break

    page = matched[:limit]
    has_more = len(matched) > limit
    for record in page:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile_id,
            action="timeline_record_read",
            entity_type="health_records",
            entity_id=record.id,
            details={"visibility": record.visibility},
        )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="timeline_read",
        entity_type="member_profiles",
        entity_id=profile_id,
        details={"count": str(len(page))},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    items = [_record_response(record, authenticated.user, session) for record in page]
    next_cursor = _encode_timeline_cursor(page[-1]) if has_more and page else None
    return TimelineResponse(
        items=items,
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.get("/records/{record_id}", response_model=RecordResponse)
def get_record(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    record: CurrentRecordRead,
) -> RecordResponse:
    response.headers["Cache-Control"] = "no-store"
    return _record_response(record, authenticated.user, session)


@router.patch("/records/{record_id}", response_model=RecordResponse)
def update_record(
    body: RecordUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
    record: CurrentRecordWrite,
) -> RecordResponse:
    if body.type is not None:
        if (
            body.type not in RECORD_TYPES
            or session.scalar(select(RecordType.id).where(RecordType.code == body.type)) is None
        ):
            raise HTTPException(status_code=422, detail="Unsupported health record type")
        record.type = body.type
    if body.recorded_at is not None:
        record.recorded_at = body.recorded_at
    if body.visibility is not None:
        record.visibility = body.visibility.value
    if body.data is not None:
        key_owner = _record_key_owner(session, record.profile_id, authenticated.user)
        encrypted = _encrypt_payload(session, key_owner, record.id, body.data)
        record.payload_nonce = encrypted.nonce
        record.encrypted_payload = encrypted.ciphertext
        record.payload_key_owner_id = key_owner.id
        record.value = None
        record.unit = None
        record.notes = None
        record.source = None
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=record.profile_id,
        action="record_updated",
        entity_type="health_records",
        entity_id=record.id,
        details={"type": record.type, "visibility": record.visibility},
    )
    session.commit()
    session.refresh(record)
    return _record_response(record, authenticated.user, session)


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
    record: CurrentRecordDelete,
) -> Response:
    record.deleted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=record.profile_id,
        action="record_deleted",
        entity_type="health_records",
        entity_id=record.id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_profile_owner_or_linked_guardian(
    session: DbSession,
    actor: User,
    profile_id: UUID,
) -> None:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id == actor.id:
        return
    decision = evaluate_access(
        session,
        actor=actor,
        profile_id=profile_id,
        visibility=Visibility.PRIVATE,
        action=Action.WRITE,
    )
    if not (
        decision.role == Role.GUARDIAN
        and decision.reason == "guardian_dependent_family_access"
        and profile.user_id is None
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the profile owner or linked Guardian can manage sharing",
        )


@router.post("/records/{record_id}/share", response_model=ShareResponse, status_code=201)
def share_record(
    record_id: UUID,
    body: ShareCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ShareResponse:
    record = session.get(HealthRecord, record_id)
    if record is None or record.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Health record not found")
    _require_profile_owner_or_linked_guardian(session, authenticated.user, record.profile_id)
    recipient = session.scalar(
        select(User).where(
            User.email == body.email,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    if recipient is None:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if recipient.id == authenticated.user.id:
        raise HTTPException(status_code=422, detail="A record cannot be shared with its owner")
    grant = ShareGrant(
        profile_id=record.profile_id,
        record_id=record.id,
        granted_to_user_id=recipient.id,
        access_role=body.role.value,
        permissions=[permission.value for permission in body.permissions],
        expires_at=body.expires_at,
    )
    record.visibility = Visibility.SELECTED.value
    session.add(grant)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=record.profile_id,
        action="record_shared",
        entity_type="health_records",
        entity_id=record.id,
        details={
            "grant_id": str(grant.id),
            "recipient_user_id": str(recipient.id),
            "role": body.role.value,
            "permissions": ",".join(permission.value for permission in body.permissions),
        },
    )
    session.commit()
    session.refresh(grant)
    return ShareResponse(
        id=grant.id,
        record_id=record.id,
        granted_to_user_id=recipient.id,
        access_role=Role(grant.access_role),
        permissions=[Action(permission) for permission in grant.permissions],
        expires_at=grant.expires_at,
    )


@router.delete(
    "/records/{record_id}/share/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_record_share(
    record_id: UUID,
    grant_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    grant = session.get(ShareGrant, grant_id)
    if grant is None or grant.record_id != record_id or grant.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Share grant not found")
    _require_profile_owner_or_linked_guardian(session, authenticated.user, grant.profile_id)
    grant.revoked_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=grant.profile_id,
        action="record_share_revoked",
        entity_type="health_records",
        entity_id=record_id,
        details={"grant_id": str(grant.id)},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
