import calendar
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.v1.routes.attachments import _object_path
from app.db.models import (
    Attachment,
    AuditLog,
    AuthSession,
    Family,
    FamilyInvite,
    FamilyMembership,
    HealthRecord,
    LoginAttempt,
    MemberProfile,
    Reminder,
    User,
)
from app.db.session import SessionLocal
from app.services.account_deletion import transfer_family_ownership
from app.services.notifications import (
    Notifier,
    dispatch_pending_notifications,
    enqueue_reminder_notification,
)


def _next_occurrence(
    value: datetime,
    recurrence: str,
    interval: int,
    *,
    anchor_day: int | None = None,
) -> datetime:
    if recurrence == "daily":
        return value + timedelta(days=interval)
    if recurrence == "weekly":
        return value + timedelta(weeks=interval)
    if recurrence == "monthly":
        month_index = value.year * 12 + value.month - 1 + interval
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        day = min(anchor_day or value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)
    raise ValueError(f"Unsupported recurrence: {recurrence}")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def process_due_reminders(
    session: Session,
    *,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> int:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    reminders = list(
        session.scalars(
            select(Reminder)
            .where(
                Reminder.deleted_at.is_(None),
                Reminder.status == "pending",
                Reminder.due_at <= current,
                (Reminder.snoozed_until.is_(None) | (Reminder.snoozed_until <= current)),
            )
            .with_for_update(skip_locked=True)
        )
    )
    generated = 0
    for reminder in reminders:
        if reminder.snoozed_until is not None and _utc(reminder.snoozed_until) > current:
            continue
        profile = session.get(MemberProfile, reminder.profile_id)
        recipients: list[str] = []
        if profile is not None and profile.user_id is not None:
            owner = session.get(User, profile.user_id)
            if owner is not None:
                recipients.append(owner.email)
        elif profile is not None:
            dependent_memberships = session.scalars(
                select(FamilyMembership).where(
                    FamilyMembership.profile_id == profile.id,
                    FamilyMembership.role == "dependent",
                    FamilyMembership.status == "active",
                    FamilyMembership.deleted_at.is_(None),
                )
            )
            family_ids = {membership.family_id for membership in dependent_memberships}
            if family_ids:
                guardians = session.scalars(
                    select(FamilyMembership).where(
                        FamilyMembership.family_id.in_(family_ids),
                        FamilyMembership.role == "guardian",
                        FamilyMembership.status == "active",
                        FamilyMembership.deleted_at.is_(None),
                    )
                )
                for membership in guardians:
                    guardian_profile = session.get(MemberProfile, membership.profile_id)
                    if guardian_profile is None or guardian_profile.user_id is None:
                        continue
                    guardian = session.get(User, guardian_profile.user_id)
                    if guardian is not None:
                        recipients.append(guardian.email)
        if not recipients:
            raise RuntimeError(
                f"Due reminder {reminder.id} has no active user or Guardian to notify"
            )
        for recipient in dict.fromkeys(recipients):
            enqueue_reminder_notification(
                session,
                reminder_id=reminder.id,
                recipient=recipient,
                message=reminder.message or reminder.title,
            )
        reminder.last_notified_at = current
        reminder.snoozed_until = None
        if reminder.recurrence == "none":
            reminder.status = "sent"
        else:
            next_due = _next_occurrence(
                _utc(reminder.due_at),
                reminder.recurrence,
                reminder.recurrence_interval,
                anchor_day=reminder.recurrence_anchor_day,
            )
            while next_due <= current:
                next_due = _next_occurrence(
                    next_due,
                    reminder.recurrence,
                    reminder.recurrence_interval,
                    anchor_day=reminder.recurrence_anchor_day,
                )
            reminder.due_at = next_due
        generated += 1
    session.commit()
    dispatch_pending_notifications(session, notifier=notifier)
    return generated


def run_reminder_scheduler() -> int:
    with SessionLocal() as session:
        return process_due_reminders(session)


def purge_expired_accounts(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = _utc(now or datetime.now(UTC))
    expired_users = list(
        session.scalars(
            select(User)
            .where(
                User.deleted_at.is_not(None),
                User.purge_after.is_not(None),
                User.purge_after <= current,
            )
            .order_by(User.purge_after, User.id)
        )
    )
    purged = 0
    for user in expired_users:
        user_id = user.id
        profiles = list(
            session.scalars(select(MemberProfile).where(MemberProfile.user_id == user_id))
        )
        profile_ids = [profile.id for profile in profiles]
        attachments = (
            list(session.scalars(select(Attachment).where(Attachment.profile_id.in_(profile_ids))))
            if profile_ids
            else []
        )
        quarantined: list[tuple[Path, Path]] = []
        try:
            for attachment in attachments:
                path = _object_path(attachment.object_key)
                if path.exists():
                    temporary_path = path.with_name(f".{path.name}.purging-{uuid.uuid4().hex}")
                    path.replace(temporary_path)
                    quarantined.append((path, temporary_path))

            for profile in profiles:
                session.delete(profile)
            session.flush()
            transfer_family_ownership(session, user_id, current)
            for family in session.scalars(
                select(Family).where(Family.created_by_user_id == user_id)
            ):
                session.delete(family)
            for invite in session.scalars(
                select(FamilyInvite).where(FamilyInvite.created_by_user_id == user_id)
            ):
                session.delete(invite)
            session.execute(
                delete(LoginAttempt).where(
                    LoginAttempt.email_hash
                    == hashlib.sha256(f"login:{user.email}".encode()).hexdigest()
                )
            )
            retains_external_key = (
                session.scalar(
                    select(HealthRecord.id)
                    .where(HealthRecord.payload_key_owner_id == user_id)
                    .limit(1)
                )
                is not None
                or session.scalar(
                    select(Attachment.id)
                    .where(Attachment.encryption_key_owner_id == user_id)
                    .limit(1)
                )
                is not None
            )
            session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
            user.email = f"purged-{user.id}@deleted.invalid"
            user.display_name = "Deleted account"
            user.password_hash = secrets.token_urlsafe(48)
            user.recovery_code_hash = None
            user.is_active = False
            user.purge_after = None
            if not retains_external_key:
                user.wrapped_data_key = None
                user.data_key_nonce = None
                user.data_key_version = None
            session.add(
                AuditLog(
                    actor_user_id=user_id,
                    action="account_purged",
                    entity_type="users",
                    entity_id=user_id,
                    details={"anonymized": True, "key_retained": retains_external_key},
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            for original_path, temporary_path in reversed(quarantined):
                if temporary_path.exists() and not original_path.exists():
                    temporary_path.replace(original_path)
            raise
        for _, temporary_path in quarantined:
            temporary_path.unlink(missing_ok=True)
        purged += 1
    return purged


def run_account_purge() -> int:
    with SessionLocal() as session:
        return purge_expired_accounts(session)


def purge_main() -> None:
    count = run_account_purge()
    print(f"Purged or anonymized {count} expired account(s)")
