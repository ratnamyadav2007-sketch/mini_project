import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import delete, select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.core.config import get_settings
from app.db.models import (
    EmergencyContact,
    HealthRecord,
    MemberProfile,
    NotificationOutbox,
    SOSEvent,
    SOSPublicAccess,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import CurrentAuthenticatedSession, DbSession
from app.security.field_encryption import EncryptedField, decrypt_field
from app.security.permissions import Action, Visibility, evaluate_access
from app.services.notifications import dispatch_pending_notifications, enqueue_sos_notifications

router = APIRouter(tags=["emergency response"])
MEDICAL_CARD_FIELDS = {
    "allergy": ("name", "reaction", "severity"),
    "condition": ("name", "status"),
    "medication": ("name", "dosage", "instructions"),
}


class SOSEventCreate(BaseModel):
    profile_id: UUID
    notes: str | None = Field(default=None, max_length=500)
    location: dict[str, float] | None = None


class SOSNotificationStatus(BaseModel):
    recipient: str
    status: str


class SOSEventResponse(BaseModel):
    id: UUID
    triggered_at: datetime
    expires_at: datetime
    public_url: str
    public_page_url: str
    notifications: list[SOSNotificationStatus]


class SOSEventResolved(BaseModel):
    status: str
    resolved_at: datetime


class EmergencyContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relationship: str = Field(min_length=1, max_length=64)
    phone_number: str = Field(min_length=3, max_length=32)
    email: EmailStr | None = None
    priority: int = Field(default=1, ge=1, le=100)


class EmergencyContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    relationship: str | None = Field(default=None, min_length=1, max_length=64)
    phone_number: str | None = Field(default=None, min_length=3, max_length=32)
    email: EmailStr | None = None
    priority: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def require_changes(self) -> "EmergencyContactUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one contact field must be provided")
        non_nullable = {"name", "relationship", "phone_number", "priority"}
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in non_nullable
        ):
            raise ValueError("Required contact fields cannot be cleared")
        return self


class EmergencyContactResponse(BaseModel):
    id: UUID
    profile_id: UUID
    name: str
    relationship: str
    phone_number: str
    email: str | None
    priority: int


class MedicalCardEntry(BaseModel):
    name: str
    details: dict[str, str]


class EmergencyHealthCard(BaseModel):
    name: str
    date_of_birth: str | None
    blood_group: str | None
    allergies: list[MedicalCardEntry]
    conditions: list[MedicalCardEntry]
    medications: list[MedicalCardEntry]
    contacts: list[EmergencyContactResponse]
    triggered_at: datetime
    expires_at: datetime


class ProfileEmergencyHealthCard(BaseModel):
    name: str
    date_of_birth: str | None
    blood_group: str | None
    allergies: list[MedicalCardEntry]
    conditions: list[MedicalCardEntry]
    medications: list[MedicalCardEntry]
    contacts: list[EmergencyContactResponse]
    generated_at: datetime


class SOSEventStatus(BaseModel):
    id: UUID
    status: str
    triggered_at: datetime
    expires_at: datetime
    notifications: list[SOSNotificationStatus]


def _signing_key() -> bytes:
    settings = get_settings()
    if settings.sos_signing_key is None:
        raise HTTPException(
            status_code=503,
            detail="SOS_SIGNING_KEY must be configured before SOS links can be created",
        )
    key = settings.sos_signing_key.get_secret_value().encode("utf-8")
    if len(key) < 32:
        raise HTTPException(
            status_code=503,
            detail="SOS_SIGNING_KEY must contain at least 32 characters",
        )
    return key


def create_public_sos_token(event_id: UUID, expires_at: datetime) -> str:
    expires_timestamp = int(
        (expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at).timestamp()
    )
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"event_id": str(event_id), "expires_at": expires_timestamp},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).rstrip(b"=")
    signature = hmac.new(_signing_key(), payload, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{payload.decode('ascii')}.{encoded_signature}"


def _decode_token(token: str) -> tuple[UUID, datetime] | None:
    try:
        payload_part, signature_part = token.split(".", maxsplit=1)
        payload_bytes = payload_part.encode("ascii")
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        expected = hmac.new(_signing_key(), payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(
            base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
        )
        event_id = UUID(payload["event_id"])
        expires_at = datetime.fromtimestamp(int(payload["expires_at"]), UTC)
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, json.JSONDecodeError):
        return None
    return event_id, expires_at


def _rate_limit_public_request(session: DbSession, request: Request) -> None:
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key_hash = hmac.new(_signing_key(), f"sos-public-client:{client_ip}".encode(), hashlib.sha256)
    digest = key_hash.hexdigest()
    now = datetime.now(UTC)
    session.execute(
        delete(SOSPublicAccess).where(
            SOSPublicAccess.window_started_at < now - timedelta(minutes=2)
        )
    )
    bucket = session.scalar(
        select(SOSPublicAccess).where(SOSPublicAccess.client_key_hash == digest).with_for_update()
    )
    if bucket is None or (now - _utc(bucket.window_started_at)) >= timedelta(minutes=1):
        if bucket is None:
            bucket = SOSPublicAccess(
                client_key_hash=digest,
                window_started_at=now,
                request_count=1,
            )
            session.add(bucket)
        else:
            bucket.window_started_at = now
            bucket.request_count = 1
    elif bucket.request_count >= settings.sos_public_rate_limit_per_minute:
        session.commit()
        raise HTTPException(status_code=429, detail="Too many emergency card requests")
    else:
        bucket.request_count += 1
    session.commit()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _require_profile_access(
    session: DbSession,
    actor: User,
    profile_id: UUID,
    action: Action,
) -> MemberProfile:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    decision = evaluate_access(
        session,
        actor=actor,
        profile_id=profile_id,
        visibility=Visibility.PRIVATE,
        action=action,
        resource="emergency_contacts",
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=actor.id,
            target_profile_id=profile_id,
            action="emergency_contact_access_denied",
            entity_type="member_profiles",
            entity_id=profile_id,
            details={"reason": decision.reason, "action": action.value},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")
    return profile


def _contact_response(contact: EmergencyContact) -> EmergencyContactResponse:
    return EmergencyContactResponse(
        id=contact.id,
        profile_id=contact.profile_id,
        name=contact.name,
        relationship=contact.relationship,
        phone_number=contact.phone_number,
        email=contact.email,
        priority=contact.priority,
    )


@router.get(
    "/profiles/{profile_id}/emergency-contacts",
    response_model=list[EmergencyContactResponse],
)
def list_emergency_contacts(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[EmergencyContactResponse]:
    _require_profile_access(session, authenticated.user, profile_id, Action.READ)
    contacts = session.scalars(
        select(EmergencyContact)
        .where(
            EmergencyContact.profile_id == profile_id,
            EmergencyContact.deleted_at.is_(None),
        )
        .order_by(EmergencyContact.priority, EmergencyContact.created_at, EmergencyContact.id)
    )
    response.headers["Cache-Control"] = "no-store"
    return [_contact_response(contact) for contact in contacts]


@router.post(
    "/profiles/{profile_id}/emergency-contacts",
    response_model=EmergencyContactResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_emergency_contact(
    profile_id: UUID,
    body: EmergencyContactCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> EmergencyContactResponse:
    _require_profile_access(session, authenticated.user, profile_id, Action.WRITE)
    contact = EmergencyContact(
        profile_id=profile_id,
        name=body.name.strip(),
        relationship=body.relationship.strip(),
        phone_number=body.phone_number.strip(),
        email=str(body.email).strip().lower() if body.email else None,
        priority=body.priority,
    )
    if not contact.name or not contact.relationship or not contact.phone_number:
        raise HTTPException(status_code=422, detail="Contact fields must not be blank")
    session.add(contact)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="emergency_contact_created",
        entity_type="emergency_contacts",
        entity_id=contact.id,
        details={"priority": contact.priority},
    )
    session.commit()
    return _contact_response(contact)


@router.patch("/emergency-contacts/{contact_id}", response_model=EmergencyContactResponse)
def update_emergency_contact(
    contact_id: UUID,
    body: EmergencyContactUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> EmergencyContactResponse:
    contact = session.get(EmergencyContact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Emergency contact not found")
    _require_profile_access(session, authenticated.user, contact.profile_id, Action.WRITE)
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if isinstance(value, str):
            value = value.strip()
            if field_name == "email" and value:
                value = value.lower()
            if not value:
                raise HTTPException(status_code=422, detail=f"{field_name} must not be blank")
        setattr(contact, field_name, value)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=contact.profile_id,
        action="emergency_contact_updated",
        entity_type="emergency_contacts",
        entity_id=contact.id,
    )
    session.commit()
    session.refresh(contact)
    return _contact_response(contact)


@router.delete("/emergency-contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_emergency_contact(
    contact_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    contact = session.get(EmergencyContact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Emergency contact not found")
    _require_profile_access(session, authenticated.user, contact.profile_id, Action.DELETE)
    contact.deleted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=contact.profile_id,
        action="emergency_contact_deleted",
        entity_type="emergency_contacts",
        entity_id=contact.id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _record_payload(session: DbSession, record: HealthRecord) -> dict[str, Any]:
    if record.encrypted_payload is not None:
        if record.payload_nonce is None or record.payload_key_owner_id is None:
            raise RuntimeError("Encrypted health record is missing encryption metadata")
        owner = session.get(User, record.payload_key_owner_id)
        if owner is None:
            raise RuntimeError("Encrypted health record key owner is unavailable")
        raw = decrypt_field(
            session,
            owner,
            f"health_records:{record.id}",
            EncryptedField(record.payload_nonce, record.encrypted_payload),
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("Health record payload must be a JSON object")
        return payload
    return {
        key: value
        for key, value in {
            "value": str(record.value) if record.value is not None else None,
            "unit": record.unit,
            "notes": record.notes,
            "source": record.source,
        }.items()
        if value is not None
    }


def build_profile_emergency_health_card(
    session: DbSession,
    profile: MemberProfile,
) -> ProfileEmergencyHealthCard:
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Emergency profile not found")
    owner = session.get(User, profile.user_id) if profile.user_id else None
    name = profile.display_name or (owner.display_name if owner else "Dependent")
    records = session.scalars(
        select(HealthRecord)
        .where(
            HealthRecord.profile_id == profile.id,
            HealthRecord.deleted_at.is_(None),
            HealthRecord.type.in_((*MEDICAL_CARD_FIELDS, "blood_group")),
        )
        .order_by(HealthRecord.recorded_at.desc(), HealthRecord.id.desc())
    )
    blood_group: str | None = None
    entries: dict[str, list[MedicalCardEntry]] = {key: [] for key in MEDICAL_CARD_FIELDS}
    for record in records:
        payload = _record_payload(session, record)
        if record.type == "blood_group":
            value = payload.get("blood_group", payload.get("value", payload.get("name")))
            if blood_group is None and value:
                blood_group = str(value)[:40]
            continue
        fields = MEDICAL_CARD_FIELDS[record.type]
        details = {
            key: str(payload[key])[:240] for key in fields if payload.get(key) not in (None, "")
        }
        if details:
            entry_name = details.pop("name", record.type.replace("_", " ").title())
            entries[record.type].append(MedicalCardEntry(name=entry_name, details=details))

    contacts = session.scalars(
        select(EmergencyContact)
        .where(
            EmergencyContact.profile_id == profile.id,
            EmergencyContact.deleted_at.is_(None),
        )
        .order_by(EmergencyContact.priority, EmergencyContact.created_at, EmergencyContact.id)
    )
    return ProfileEmergencyHealthCard(
        name=name[:120],
        date_of_birth=profile.date_of_birth.isoformat() if profile.date_of_birth else None,
        blood_group=blood_group,
        allergies=entries["allergy"],
        conditions=entries["condition"],
        medications=entries["medication"],
        contacts=[_contact_response(contact) for contact in contacts],
        generated_at=datetime.now(UTC),
    )


def build_emergency_health_card(
    session: DbSession,
    event: SOSEvent,
) -> EmergencyHealthCard:
    profile = session.get(MemberProfile, event.profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Emergency profile not found")
    expires_at = event.public_expires_at
    if expires_at is None:
        raise RuntimeError("SOS event has no public-link expiry")
    card = build_profile_emergency_health_card(session, profile)
    return EmergencyHealthCard(
        **card.model_dump(),
        triggered_at=_utc(event.triggered_at),
        expires_at=_utc(expires_at),
    )


@router.get(
    "/profiles/{profile_id}/emergency-card",
    response_model=ProfileEmergencyHealthCard,
)
def get_profile_emergency_health_card(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> ProfileEmergencyHealthCard:
    profile = _require_profile_access(session, authenticated.user, profile_id, Action.READ)
    response.headers["Cache-Control"] = "no-store"
    return build_profile_emergency_health_card(session, profile)


@router.post("/sos", response_model=SOSEventResponse, status_code=status.HTTP_201_CREATED)
def create_sos_event(
    body: SOSEventCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> SOSEventResponse:
    _signing_key()
    profile = _require_profile_access(
        session,
        authenticated.user,
        body.profile_id,
        Action.WRITE,
    )
    triggered_at = datetime.now(UTC)
    expires_at = triggered_at + timedelta(seconds=get_settings().sos_link_ttl_seconds)
    public_base_url = get_settings().public_base_url.rstrip("/")
    event = SOSEvent(
        profile_id=profile.id,
        triggered_at=triggered_at,
        status="active",
        notes=body.notes,
        location=body.location,
        visibility=Visibility.PRIVATE.value,
        public_expires_at=expires_at,
    )
    session.add(event)
    session.flush()
    token = create_public_sos_token(event.id, expires_at)
    event.public_token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    contacts = list(
        session.scalars(
            select(EmergencyContact)
            .where(
                EmergencyContact.profile_id == profile.id,
                EmergencyContact.deleted_at.is_(None),
            )
            .order_by(EmergencyContact.priority, EmergencyContact.created_at, EmergencyContact.id)
        )
    )
    recipients = [
        f"email:{contact.email}" if contact.email else f"phone:{contact.phone_number}"
        for contact in contacts
    ]
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="sos_triggered",
        entity_type="sos_events",
        entity_id=event.id,
        details={"contact_count": str(len(recipients))},
    )
    items = enqueue_sos_notifications(
        session,
        event_id=event.id,
        recipients=recipients,
        base_url=public_base_url,
    )
    session.commit()

    dispatch_pending_notifications(session)
    session.refresh(event)
    session.refresh(items[0]) if items else None
    return SOSEventResponse(
        id=event.id,
        triggered_at=event.triggered_at,
        expires_at=expires_at,
        public_url=f"{public_base_url}/api/v1/sos/{token}",
        public_page_url=f"{public_base_url}/public-sos.html?token={token}",
        notifications=[
            SOSNotificationStatus(recipient=item.recipient, status=item.status) for item in items
        ],
    )


@router.post("/sos/{event_id}/resolve", response_model=SOSEventResolved)
def resolve_sos_event(
    event_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> SOSEventResolved:
    event = session.get(SOSEvent, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="SOS event not found")
    _require_profile_access(session, authenticated.user, event.profile_id, Action.WRITE)
    if event.resolved_at is not None:
        raise HTTPException(status_code=409, detail="SOS event is already resolved")
    resolved_at = datetime.now(UTC)
    event.resolved_at = resolved_at
    event.status = "resolved"
    event.public_token_hash = None
    pending_notifications = session.scalars(
        select(NotificationOutbox).where(
            NotificationOutbox.sos_event_id == event.id,
            NotificationOutbox.status == "pending",
        )
    )
    for notification in pending_notifications:
        notification.status = "cancelled"
        notification.last_error = "SOS event was resolved before notification delivery"
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=event.profile_id,
        action="sos_resolved",
        entity_type="sos_events",
        entity_id=event.id,
    )
    session.commit()
    return SOSEventResolved(status=event.status, resolved_at=resolved_at)


@router.get("/sos/events/{event_id}", response_model=SOSEventStatus)
@router.get("/sos/{event_id:uuid}", response_model=SOSEventStatus, include_in_schema=False)
def get_sos_event_status(
    event_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> SOSEventStatus:
    event = session.get(SOSEvent, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="SOS event not found")
    _require_profile_access(session, authenticated.user, event.profile_id, Action.READ)
    notifications = list(
        session.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.sos_event_id == event.id)
            .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
        )
    )
    response.headers["Cache-Control"] = "no-store"
    expires_at = event.public_expires_at
    if expires_at is None:
        raise RuntimeError("SOS event has no public-link expiry")
    return SOSEventStatus(
        id=event.id,
        status=event.status,
        triggered_at=_utc(event.triggered_at),
        expires_at=_utc(expires_at),
        notifications=[
            SOSNotificationStatus(recipient=item.recipient, status=item.status)
            for item in notifications
        ],
    )


@router.get("/sos/{token}", response_model=EmergencyHealthCard)
def get_public_sos_card(
    token: str,
    request: Request,
    response: Response,
    session: DbSession,
) -> EmergencyHealthCard:
    _rate_limit_public_request(session, request)
    decoded = _decode_token(token)
    if decoded is None:
        raise HTTPException(status_code=404, detail="Emergency card not found")
    event_id, token_expiry = decoded
    event = session.get(SOSEvent, event_id)
    supplied_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    if (
        event is None
        or event.deleted_at is not None
        or event.resolved_at is not None
        or event.public_token_hash is None
        or event.public_expires_at is None
        or not hmac.compare_digest(event.public_token_hash, supplied_hash)
        or token_expiry <= datetime.now(UTC)
        or _utc(event.public_expires_at) <= datetime.now(UTC)
        or int(_utc(event.public_expires_at).timestamp()) != int(token_expiry.timestamp())
    ):
        raise HTTPException(status_code=404, detail="Emergency card not found")
    append_audit_event(
        session,
        actor_user_id=None,
        target_profile_id=event.profile_id,
        action="public_sos_card_viewed",
        entity_type="sos_events",
        entity_id=event.id,
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return build_emergency_health_card(session, event)
