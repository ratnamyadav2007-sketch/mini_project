import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    AuthCsrfChallenge,
    AuthSession,
    LoginAttempt,
    MemberProfile,
    User,
)
from app.db.repository import utc_now
from app.security.audit import append_audit_event

password_hash = PasswordHash.recommended()
_dummy_password_hash = password_hash.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_csrf_token(raw_session_token: str) -> str:
    return hmac.new(
        raw_session_token.encode("utf-8"),
        b"fieldnote-session-csrf",
        hashlib.sha256,
    ).hexdigest()


def add_audit(
    session: Session,
    action: str,
    user_id: UUID | None,
    details: dict[str, str] | None = None,
) -> None:
    append_audit_event(
        session,
        actor_user_id=user_id,
        action=action,
        entity_type="user",
        entity_id=user_id,
        details=details,
    )


def create_auth_session(session: Session, user: User) -> tuple[str, str]:
    settings = get_settings()
    now = utc_now()
    for previous in session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .with_for_update()
    ):
        previous.revoked_at = now

    raw_token = secrets.token_urlsafe(32)
    csrf_token = session_csrf_token(raw_token)
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=digest(raw_token),
            csrf_token_hash=digest(csrf_token),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.auth_session_ttl_seconds),
        )
    )
    return raw_token, csrf_token


def consume_csrf_challenge(session: Session, token: str) -> bool:
    challenge = session.scalar(
        select(AuthCsrfChallenge)
        .where(AuthCsrfChallenge.token_hash == digest(token))
        .with_for_update()
    )
    if challenge is None or normalize_expiry(challenge.expires_at) <= utc_now():
        return False
    session.delete(challenge)
    return True


def add_csrf_challenge(session: Session) -> str:
    token = secrets.token_urlsafe(32)
    settings = get_settings()
    now = utc_now()
    session.execute(delete(AuthCsrfChallenge).where(AuthCsrfChallenge.expires_at <= now))
    session.add(
        AuthCsrfChallenge(
            token_hash=digest(token),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.auth_csrf_challenge_ttl_seconds),
        )
    )
    session.commit()
    return token


def record_failed_login(
    session: Session,
    email: str,
    user: User | None,
) -> bool:
    settings = get_settings()
    now = utc_now()
    email_hash = digest(f"login:{email}")
    attempt = session.scalar(
        select(LoginAttempt).where(LoginAttempt.email_hash == email_hash).with_for_update()
    )
    if attempt is None:
        values = {
            "email_hash": email_hash,
            "failed_attempts": 0,
            "window_started_at": now,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            insert = postgresql_insert(LoginAttempt)
        elif dialect == "sqlite":
            insert = sqlite_insert(LoginAttempt)
        else:
            raise RuntimeError(f"Unsupported database dialect for login throttling: {dialect}")
        session.execute(
            insert.values(**values).on_conflict_do_nothing(index_elements=["email_hash"])
        )
        attempt = session.scalar(
            select(LoginAttempt).where(LoginAttempt.email_hash == email_hash).with_for_update()
        )
        if attempt is None:
            raise RuntimeError("Failed to initialize login throttling state")

    if attempt.locked_until is not None and normalize_expiry(attempt.locked_until) > now:
        locked = True
    else:
        locked = False
        if now - normalize_expiry(attempt.window_started_at) >= timedelta(
            seconds=settings.auth_login_lockout_seconds
        ):
            attempt.failed_attempts = 0
            attempt.window_started_at = now
        attempt.failed_attempts += 1
        if attempt.failed_attempts >= settings.auth_login_max_failures:
            attempt.locked_until = now + timedelta(seconds=settings.auth_login_lockout_seconds)
            locked = True

    add_audit(
        session,
        "login_failed",
        user.id if user is not None else None,
        {"reason": "locked_out" if locked else "invalid_credentials"},
    )
    session.commit()
    return locked


def login_is_locked(session: Session, email: str) -> bool:
    attempt = session.get(LoginAttempt, digest(f"login:{email}"))
    return bool(
        attempt is not None
        and attempt.locked_until is not None
        and normalize_expiry(attempt.locked_until) > utc_now()
    )


def reset_failed_logins(session: Session, email: str) -> None:
    session.execute(delete(LoginAttempt).where(LoginAttempt.email_hash == digest(f"login:{email}")))


def normalize_expiry(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def create_user(
    session: Session,
    email: str,
    raw_password: str,
    display_name: str,
) -> tuple[User, str]:
    recovery_code = secrets.token_urlsafe(24)
    user = User(
        email=email,
        password_hash=password_hash.hash(raw_password),
        recovery_code_hash=digest(recovery_code),
        display_name=display_name,
    )
    session.add(user)
    session.flush()
    session.add(
        MemberProfile(
            user_id=user.id,
            preferences={
                "relationship": "you",
                "avatar_color": "#456c58",
                "avatar_wash": "#e5ede5",
                "allergies": [],
                "conditions": [],
                "onboarding_step": 0,
                "onboarding_complete": False,
            },
        )
    )
    return user, recovery_code


def verify_credentials(user: User | None, raw_password: str) -> bool:
    encoded = user.password_hash if user is not None else _dummy_password_hash
    valid = password_hash.verify(raw_password, encoded)
    return user is not None and user.is_active and user.deleted_at is None and valid


def verify_recovery_code(user: User | None, recovery_code: str) -> bool:
    return bool(
        user is not None
        and user.recovery_code_hash is not None
        and secrets.compare_digest(user.recovery_code_hash, digest(recovery_code))
    )
