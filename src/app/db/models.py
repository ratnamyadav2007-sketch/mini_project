from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VisibilityMixin:
    visibility: Mapped[str] = mapped_column(
        String(16), default="private", server_default="private", nullable=False
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_deleted_at_purge_after", "deleted_at", "purge_after"),)

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    recovery_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wrapped_data_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    data_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    data_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemberProfile(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "member_profiles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Family(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "families"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class FamilyMembership(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "family_memberships"
    __table_args__ = (
        UniqueConstraint("family_id", "profile_id", name="uq_family_membership_family_profile"),
    )

    family_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class FamilyInvite(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "family_invites"

    family_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("families.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invite_code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecordType(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "record_types"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    default_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class HealthRecord(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "health_records"
    __table_args__ = (
        Index("ix_health_records_profile_id_recorded_at", "profile_id", "recorded_at"),
        Index("ix_health_records_profile_type_recorded_at", "profile_id", "type", "recorded_at"),
    )

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        String(64), ForeignKey("record_types.code", ondelete="RESTRICT"), index=True, nullable=False
    )
    value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_payload: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payload_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payload_key_owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReferenceRange(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "reference_ranges"
    __table_args__ = (
        CheckConstraint(
            "age_min IS NULL OR age_min BETWEEN 0 AND 120", name="ck_reference_age_min"
        ),
        CheckConstraint(
            "age_max IS NULL OR age_max BETWEEN 0 AND 120", name="ck_reference_age_max"
        ),
        CheckConstraint(
            "age_min IS NULL OR age_max IS NULL OR age_min <= age_max",
            name="ck_reference_age_order",
        ),
        CheckConstraint(
            "lower_bound IS NULL OR upper_bound IS NULL OR lower_bound < upper_bound",
            name="ck_reference_bound_order",
        ),
        Index(
            "ix_reference_ranges_demographic_lookup",
            "record_type_id",
            "unit",
            "sex",
            "age_min",
            "age_max",
        ),
    )

    record_type_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("record_types.id", ondelete="CASCADE"), nullable=False
    )
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    sex: Mapped[str | None] = mapped_column(String(32), nullable=True)
    age_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    population: Mapped[str] = mapped_column(String(120), nullable=False)
    source_note: Mapped[str] = mapped_column(Text, nullable=False)


class Attachment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "attachments"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_records.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    encryption_key_owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ShareGrant(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "share_grants"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    granted_to_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    access_role: Mapped[str] = mapped_column(
        String(32), default="viewer", server_default="viewer", nullable=False
    )
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmergencyContact(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "emergency_contacts"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    relationship: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointments_profile_starts_at", "profile_id", "starts_at"),)

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Reminder(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_status_due_at_profile", "status", "due_at", "profile_id"),
    )

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    appointment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    recurrence_interval: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recurrence_anchor_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DietPlan(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "diet_plans"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    macro_targets: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    excluded_foods: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class Food(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "foods"

    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    serving_basis: Mapped[str] = mapped_column(String(32), default="per 100 g", nullable=False)
    nutrients_per_100g: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_conditions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_note: Mapped[str] = mapped_column(Text, nullable=False)


class MealItem(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "meal_items"

    diet_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("diet_plans.id", ondelete="CASCADE"), nullable=False
    )
    food_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    serving_size_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Goal(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "goals"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    goal_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)


class GoalLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "goal_logs"
    __table_args__ = (
        UniqueConstraint("goal_id", "logged_on", name="uq_goal_logs_goal_id_logged_on"),
    )

    goal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    logged_on: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)


class BadgeAward(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "badge_awards"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    badge_code: Mapped[str] = mapped_column(String(64), nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SOSEvent(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, VisibilityMixin, Base):
    __tablename__ = "sos_events"
    __table_args__ = (Index("ix_sos_events_public_token_hash", "public_token_hash", unique=True),)

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    location: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    public_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_outbox"

    sos_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sos_events.id", ondelete="CASCADE"), nullable=True
    )
    reminder_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("reminders.id", ondelete="CASCADE"), nullable=True
    )
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SOSPublicAccess(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sos_public_access"

    client_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_target_profile_created_at", "target_profile_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_profile_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ApiRateLimit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "api_rate_limits"
    __table_args__ = (Index("ix_api_rate_limits_window_started_at", "window_started_at"),)

    bucket_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuthSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthCsrfChallenge(Base):
    __tablename__ = "auth_csrf_challenges"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    email_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
