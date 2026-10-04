from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.db.models import Appointment, MemberProfile, NotificationOutbox, Reminder, User
from app.security.audit import append_audit_event
from app.security.dependencies import CurrentAuthenticatedSession, DbSession
from app.security.permissions import Action, Visibility, evaluate_access

router = APIRouter(tags=["appointments and reminders"])
Recurrence = Literal["none", "daily", "weekly", "monthly"]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AppointmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"
    notes: str | None = Field(default=None, max_length=4000)
    visibility: Visibility = Visibility.PRIVATE

    @field_validator("title")
    @classmethod
    def non_blank_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Appointment timestamps must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_end_time(self) -> "AppointmentCreate":
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class AppointmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=255)
    status: Literal["scheduled", "completed", "cancelled"] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    visibility: Visibility | None = None

    @field_validator("title")
    @classmethod
    def non_blank_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Appointment timestamps must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_changes(self) -> "AppointmentUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        required_when_set = {"title", "starts_at", "status", "visibility"}
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in required_when_set
        ):
            raise ValueError("Non-null appointment fields cannot be cleared")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be later than starts_at")
        return self


class AppointmentResponse(BaseModel):
    id: UUID
    profile_id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None
    location: str | None
    status: str
    notes: str | None
    visibility: Visibility


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    due_at: datetime
    appointment_id: UUID | None = None
    message: str | None = Field(default=None, max_length=2000)
    recurrence: Recurrence = "none"
    recurrence_interval: int = Field(default=1, ge=1, le=365)
    visibility: Visibility = Visibility.PRIVATE

    @field_validator("title")
    @classmethod
    def non_blank_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("due_at must include a timezone")
        return value.astimezone(UTC)


class ReminderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    due_at: datetime | None = None
    appointment_id: UUID | None = None
    message: str | None = Field(default=None, max_length=2000)
    recurrence: Recurrence | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)
    visibility: Visibility | None = None

    @field_validator("title")
    @classmethod
    def non_blank_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def require_changes(self) -> "ReminderUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        required_when_set = {
            "title",
            "due_at",
            "recurrence",
            "recurrence_interval",
            "visibility",
        }
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in required_when_set
        ):
            raise ValueError("Non-null reminder fields cannot be cleared")
        return self


class ReminderResponse(BaseModel):
    id: UUID
    profile_id: UUID
    appointment_id: UUID | None
    title: str
    due_at: datetime
    status: str
    message: str | None
    recurrence: str
    recurrence_interval: int
    snoozed_until: datetime | None
    visibility: Visibility


class ReminderDueResponse(BaseModel):
    notification_id: UUID | None
    reminder_id: UUID
    profile_id: UUID
    title: str
    message: str
    due_at: datetime
    delivered_at: datetime | None
    notification_status: str


class SnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=10080)


def _can_access(
    session: DbSession,
    actor: User,
    profile_id: UUID,
    visibility: str,
    action: Action,
    resource: str,
    record_id: UUID | None = None,
) -> bool:
    decision = evaluate_access(
        session,
        actor=actor,
        profile_id=profile_id,
        visibility=visibility,
        action=action,
        record_id=record_id,
        resource=resource,
    )
    return decision.allowed


def _require_access(
    session: DbSession,
    actor: User,
    profile_id: UUID,
    visibility: str,
    action: Action,
    resource: str,
    record_id: UUID | None = None,
) -> None:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _can_access(session, actor, profile_id, visibility, action, resource, record_id):
        append_audit_event(
            session,
            actor_user_id=actor.id,
            target_profile_id=profile_id,
            action=f"{resource}_access_denied",
            entity_type=resource,
            entity_id=record_id,
            details={"action": action.value},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")


def _appointment_response(appointment: Appointment) -> AppointmentResponse:
    return AppointmentResponse(
        id=appointment.id,
        profile_id=appointment.profile_id,
        title=appointment.title,
        starts_at=_utc(appointment.starts_at),
        ends_at=_utc(appointment.ends_at) if appointment.ends_at is not None else None,
        location=appointment.location,
        status=appointment.status,
        notes=appointment.notes,
        visibility=Visibility(appointment.visibility),
    )


def _reminder_response(reminder: Reminder) -> ReminderResponse:
    return ReminderResponse(
        id=reminder.id,
        profile_id=reminder.profile_id,
        appointment_id=reminder.appointment_id,
        title=reminder.title,
        due_at=_utc(reminder.due_at),
        status=reminder.status,
        message=reminder.message,
        recurrence=reminder.recurrence,
        recurrence_interval=reminder.recurrence_interval,
        snoozed_until=_utc(reminder.snoozed_until) if reminder.snoozed_until else None,
        visibility=Visibility(reminder.visibility),
    )


@router.post(
    "/profiles/{profile_id}/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    profile_id: UUID,
    body: AppointmentCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> AppointmentResponse:
    _require_access(
        session,
        authenticated.user,
        profile_id,
        body.visibility.value,
        Action.WRITE,
        "appointments",
    )
    appointment = Appointment(
        profile_id=profile_id,
        title=body.title,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        location=body.location.strip() if body.location else None,
        status=body.status,
        notes=body.notes,
        visibility=body.visibility.value,
    )
    session.add(appointment)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="appointment_created",
        entity_type="appointments",
        entity_id=appointment.id,
        details={"visibility": appointment.visibility},
    )
    session.commit()
    return _appointment_response(appointment)


@router.get("/profiles/{profile_id}/appointments", response_model=list[AppointmentResponse])
def list_appointments(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> list[AppointmentResponse]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if start is not None and (start.tzinfo is None or start.utcoffset() is None):
        raise HTTPException(status_code=422, detail="start must include a timezone")
    if end is not None and (end.tzinfo is None or end.utcoffset() is None):
        raise HTTPException(status_code=422, detail="end must include a timezone")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=422, detail="start must precede end")
    statement = (
        select(Appointment)
        .where(
            Appointment.profile_id == profile_id,
            Appointment.deleted_at.is_(None),
        )
        .order_by(Appointment.starts_at, Appointment.id)
    )
    if start is not None:
        statement = statement.where(Appointment.starts_at >= start.astimezone(UTC))
    if end is not None:
        statement = statement.where(Appointment.starts_at < end.astimezone(UTC))
    appointments = session.scalars(statement)
    visible = [
        appointment
        for appointment in appointments
        if _can_access(
            session,
            authenticated.user,
            profile_id,
            appointment.visibility,
            Action.READ,
            "appointments",
            appointment.id,
        )
    ][:limit]
    response.headers["Cache-Control"] = "no-store"
    return [_appointment_response(item) for item in visible]


@router.get("/appointments", response_model=list[AppointmentResponse])
def list_calendar_appointments(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    profile_id: UUID,
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int = 100,
) -> list[AppointmentResponse]:
    return list_appointments(
        profile_id,
        response,
        authenticated,
        session,
        start=from_date,
        end=to,
        limit=limit,
    )


@router.get("/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> AppointmentResponse:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None or appointment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    _require_access(
        session,
        authenticated.user,
        appointment.profile_id,
        appointment.visibility,
        Action.READ,
        "appointments",
        appointment.id,
    )
    response.headers["Cache-Control"] = "no-store"
    return _appointment_response(appointment)


@router.patch("/appointments/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: UUID,
    body: AppointmentUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> AppointmentResponse:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None or appointment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    _require_access(
        session,
        authenticated.user,
        appointment.profile_id,
        appointment.visibility,
        Action.WRITE,
        "appointments",
        appointment.id,
    )
    fields = body.model_dump(exclude_unset=True)
    starts_at = fields.get("starts_at", appointment.starts_at)
    ends_at = fields.get("ends_at", appointment.ends_at)
    if starts_at is not None and ends_at is not None and _utc(ends_at) <= _utc(starts_at):
        raise HTTPException(status_code=422, detail="ends_at must be later than starts_at")
    if "visibility" in fields and not _can_access(
        session,
        authenticated.user,
        appointment.profile_id,
        fields["visibility"].value,
        Action.WRITE,
        "appointments",
    ):
        raise HTTPException(status_code=403, detail="Cannot set the requested visibility")
    for field_name, value in fields.items():
        if field_name == "visibility":
            value = value.value
        setattr(appointment, field_name, value)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=appointment.profile_id,
        action="appointment_updated",
        entity_type="appointments",
        entity_id=appointment.id,
    )
    session.commit()
    session.refresh(appointment)
    return _appointment_response(appointment)


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(
    appointment_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None or appointment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    _require_access(
        session,
        authenticated.user,
        appointment.profile_id,
        appointment.visibility,
        Action.DELETE,
        "appointments",
        appointment.id,
    )
    appointment.deleted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=appointment.profile_id,
        action="appointment_deleted",
        entity_type="appointments",
        entity_id=appointment.id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


@router.get("/profiles/{profile_id}/appointments.ics")
def export_appointments_ics(
    profile_id: UUID,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> Response:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    appointments = session.scalars(
        select(Appointment)
        .where(
            Appointment.profile_id == profile_id,
            Appointment.deleted_at.is_(None),
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.starts_at, Appointment.id)
    )
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Backend API//Appointments//EN"]
    for appointment in appointments:
        if not _can_access(
            session,
            authenticated.user,
            profile_id,
            appointment.visibility,
            Action.READ,
            "appointments",
            appointment.id,
        ):
            continue
        start = _utc(appointment.starts_at)
        end = _utc(appointment.ends_at) if appointment.ends_at else start
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{appointment.id}@backend.local",
                f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{_ics_escape(appointment.title)}",
                f"DESCRIPTION:{_ics_escape(appointment.notes or '')}",
            ]
        )
        if appointment.location:
            lines.append(f"LOCATION:{_ics_escape(appointment.location)}")
        lines.extend(["END:VEVENT"])
    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="appointments-{profile_id}.ics"',
        },
    )


@router.post(
    "/profiles/{profile_id}/reminders",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reminder(
    profile_id: UUID,
    body: ReminderCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ReminderResponse:
    _require_access(
        session,
        authenticated.user,
        profile_id,
        body.visibility.value,
        Action.WRITE,
        "reminders",
    )
    if body.appointment_id is not None:
        appointment = session.get(Appointment, body.appointment_id)
        if (
            appointment is None
            or appointment.profile_id != profile_id
            or appointment.deleted_at is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="Appointment is not active for this profile",
            )
    reminder = Reminder(
        profile_id=profile_id,
        appointment_id=body.appointment_id,
        title=body.title,
        due_at=body.due_at,
        message=body.message,
        recurrence=body.recurrence,
        recurrence_interval=body.recurrence_interval,
        recurrence_anchor_day=body.due_at.day,
        visibility=body.visibility.value,
    )
    session.add(reminder)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="reminder_created",
        entity_type="reminders",
        entity_id=reminder.id,
        details={"recurrence": reminder.recurrence},
    )
    session.commit()
    return _reminder_response(reminder)


@router.get("/profiles/{profile_id}/reminders", response_model=list[ReminderResponse])
def list_reminders(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[ReminderResponse]:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    reminders = session.scalars(
        select(Reminder)
        .where(Reminder.profile_id == profile_id, Reminder.deleted_at.is_(None))
        .order_by(Reminder.due_at, Reminder.id)
    )
    visible = [
        reminder
        for reminder in reminders
        if _can_access(
            session,
            authenticated.user,
            profile_id,
            reminder.visibility,
            Action.READ,
            "reminders",
            reminder.id,
        )
    ]
    response.headers["Cache-Control"] = "no-store"
    return [_reminder_response(reminder) for reminder in visible]


@router.get("/reminders/due", response_model=list[ReminderDueResponse])
def list_due_reminders(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    since: datetime | None = None,
) -> list[ReminderDueResponse]:
    now = datetime.now(UTC)
    if since is not None and (since.tzinfo is None or since.utcoffset() is None):
        raise HTTPException(status_code=422, detail="since must include a timezone")
    since_utc = _utc(since) if since is not None else now - timedelta(minutes=2)
    due_reminders = list(
        session.scalars(
            select(Reminder)
            .where(
                Reminder.deleted_at.is_(None),
                Reminder.status == "pending",
                Reminder.due_at <= now,
                (Reminder.snoozed_until.is_(None) | (Reminder.snoozed_until <= now)),
                (
                    Reminder.last_notified_at.is_(None)
                    | (Reminder.due_at > Reminder.last_notified_at)
                ),
            )
            .order_by(Reminder.due_at, Reminder.id)
        )
    )
    items: list[ReminderDueResponse] = []
    for reminder in due_reminders:
        if not _can_access(
            session,
            authenticated.user,
            reminder.profile_id,
            reminder.visibility,
            Action.READ,
            "reminders",
            reminder.id,
        ):
            continue
        items.append(
            ReminderDueResponse(
                notification_id=None,
                reminder_id=reminder.id,
                profile_id=reminder.profile_id,
                title=reminder.title,
                message=reminder.message or reminder.title,
                due_at=_utc(reminder.due_at),
                delivered_at=None,
                notification_status="pending",
            )
        )

    delivered = session.scalars(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.reminder_id.is_not(None),
            NotificationOutbox.recipient == authenticated.user.email,
            NotificationOutbox.status == "delivered",
            NotificationOutbox.delivered_at > since_utc,
        )
        .order_by(NotificationOutbox.delivered_at, NotificationOutbox.id)
    )
    for notification in delivered:
        reminder = session.get(Reminder, notification.reminder_id)
        if (
            reminder is None
            or reminder.deleted_at is not None
            or not _can_access(
                session,
                authenticated.user,
                reminder.profile_id,
                reminder.visibility,
                Action.READ,
                "reminders",
                reminder.id,
            )
        ):
            continue
        items.append(
            ReminderDueResponse(
                notification_id=notification.id,
                reminder_id=reminder.id,
                profile_id=reminder.profile_id,
                title=reminder.title,
                message=str(notification.payload.get("message", reminder.title)),
                due_at=_utc(reminder.due_at),
                delivered_at=_utc(notification.delivered_at),
                notification_status=notification.status,
            )
        )
    response.headers["Cache-Control"] = "no-store"
    return items


@router.patch("/reminders/{reminder_id}", response_model=ReminderResponse)
def update_reminder(
    reminder_id: UUID,
    body: ReminderUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ReminderResponse:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None or reminder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    _require_access(
        session,
        authenticated.user,
        reminder.profile_id,
        reminder.visibility,
        Action.WRITE,
        "reminders",
        reminder.id,
    )
    fields = body.model_dump(exclude_unset=True)
    if fields.get("appointment_id") is not None:
        appointment = session.get(Appointment, fields["appointment_id"])
        if (
            appointment is None
            or appointment.profile_id != reminder.profile_id
            or appointment.deleted_at is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="Appointment is not active for this profile",
            )
    for field_name, value in fields.items():
        if field_name == "visibility":
            value = value.value
        if field_name == "due_at" and value is not None:
            reminder.recurrence_anchor_day = value.day
        setattr(reminder, field_name, value)
    reminder.status = "pending"
    reminder.snoozed_until = None
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=reminder.profile_id,
        action="reminder_updated",
        entity_type="reminders",
        entity_id=reminder.id,
    )
    session.commit()
    session.refresh(reminder)
    return _reminder_response(reminder)


@router.post("/reminders/{reminder_id}/snooze", response_model=ReminderResponse)
def snooze_reminder(
    reminder_id: UUID,
    body: SnoozeRequest,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ReminderResponse:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None or reminder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    _require_access(
        session,
        authenticated.user,
        reminder.profile_id,
        reminder.visibility,
        Action.WRITE,
        "reminders",
        reminder.id,
    )
    if reminder.status not in {"pending", "sent"}:
        raise HTTPException(status_code=409, detail="Only pending or sent reminders can be snoozed")
    reminder.status = "pending"
    reminder.snoozed_until = datetime.now(UTC) + timedelta(minutes=body.minutes)
    reminder.last_notified_at = None
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=reminder.profile_id,
        action="reminder_snoozed",
        entity_type="reminders",
        entity_id=reminder.id,
        details={"minutes": body.minutes},
    )
    session.commit()
    session.refresh(reminder)
    return _reminder_response(reminder)


@router.post("/reminders/{reminder_id}/complete", response_model=ReminderResponse)
def complete_reminder(
    reminder_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ReminderResponse:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None or reminder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    _require_access(
        session,
        authenticated.user,
        reminder.profile_id,
        reminder.visibility,
        Action.WRITE,
        "reminders",
        reminder.id,
    )
    reminder.status = "completed"
    reminder.snoozed_until = None
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=reminder.profile_id,
        action="reminder_completed",
        entity_type="reminders",
        entity_id=reminder.id,
    )
    session.commit()
    session.refresh(reminder)
    return _reminder_response(reminder)


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(
    reminder_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None or reminder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    _require_access(
        session,
        authenticated.user,
        reminder.profile_id,
        reminder.visibility,
        Action.DELETE,
        "reminders",
        reminder.id,
    )
    reminder.deleted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=reminder.profile_id,
        action="reminder_deleted",
        entity_type="reminders",
        entity_id=reminder.id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
