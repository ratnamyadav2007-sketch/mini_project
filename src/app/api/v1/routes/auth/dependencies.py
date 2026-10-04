import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User
from app.db.session import get_db

SESSION_COOKIE_NAME = "session"
CSRF_CHALLENGE_COOKIE_NAME = "csrf_challenge"
CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_COOKIE_PATH = "/api/v1"
DbSession = Annotated[Session, Depends(get_db)]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    session: AuthSession


def require_csrf_header(request: Request) -> str:
    token = request.headers.get(CSRF_HEADER_NAME, "")
    if not token or len(token) > 256:
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    return token


def get_authenticated_session(
    request: Request,
    session: DbSession,
) -> AuthenticatedSession:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_digest(raw_token))
    )
    now = datetime.now(UTC)
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or utc_datetime(auth_session.expires_at) <= now
    ):
        raise HTTPException(status_code=401, detail="Authentication required")
    user = session.get(User, auth_session.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return AuthenticatedSession(user=user, session=auth_session)


def require_session_csrf(
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
) -> AuthenticatedSession:
    token = require_csrf_header(request)
    expected_hash = token_digest(token)
    if not hmac.compare_digest(expected_hash, authenticated.session.csrf_token_hash):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    return authenticated


CurrentAuthenticatedSession = Annotated[AuthenticatedSession, Depends(get_authenticated_session)]
CurrentCsrfAuthenticatedSession = Annotated[AuthenticatedSession, Depends(require_session_csrf)]


def set_session_cookie(response: Response, raw_token: str, max_age: int) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def set_csrf_challenge_cookie(response: Response, raw_token: str, max_age: int) -> None:
    from app.core.config import get_settings

    response.set_cookie(
        key=CSRF_CHALLENGE_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_csrf_challenge_cookie(response: Response) -> None:
    from app.core.config import get_settings

    response.delete_cookie(
        key=CSRF_CHALLENGE_COOKIE_NAME,
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_session_cookie(response: Response) -> None:
    from app.core.config import get_settings

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )
