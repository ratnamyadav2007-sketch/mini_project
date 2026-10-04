import hmac
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.routes.auth.dependencies import (
    CSRF_CHALLENGE_COOKIE_NAME,
    CurrentAuthenticatedSession,
    CurrentCsrfAuthenticatedSession,
    DbSession,
    clear_csrf_challenge_cookie,
    clear_session_cookie,
    require_csrf_header,
    set_csrf_challenge_cookie,
    set_session_cookie,
)
from app.api.v1.routes.auth.schemas import (
    AuthUserResponse,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.api.v1.routes.auth.service import (
    add_audit,
    add_csrf_challenge,
    consume_csrf_challenge,
    create_auth_session,
    create_user,
    digest,
    login_is_locked,
    password_hash,
    record_failed_login,
    reset_failed_logins,
    session_csrf_token,
    verify_credentials,
    verify_recovery_code,
)
from app.core.config import get_settings
from app.db.models import AuthSession, LoginAttempt, User

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_csrf_challenge(request: Request, response: Response, session: Session) -> str:
    token = require_csrf_header(request)
    cookie_token = request.cookies.get(CSRF_CHALLENGE_COOKIE_NAME, "")
    if not cookie_token or not hmac.compare_digest(token, cookie_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF challenge cookie")
    if not consume_csrf_challenge(session, token):
        raise HTTPException(status_code=403, detail="Invalid or expired CSRF token")
    session.commit()
    clear_csrf_challenge_cookie(response)
    return token


def _session_response(
    response: Response,
    raw_session_token: str,
    csrf_token: str,
    user: User,
    **additional: str,
) -> dict[str, Any]:
    settings = get_settings()
    set_session_cookie(response, raw_session_token, settings.auth_session_ttl_seconds)
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": AuthUserResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
        ).model_dump(),
        "csrf_token": csrf_token,
        **additional,
    }


@router.get("/csrf")
def issue_csrf_challenge(response: Response, session: DbSession) -> dict[str, str]:
    settings = get_settings()
    csrf_token = add_csrf_challenge(session)
    set_csrf_challenge_cookie(
        response,
        csrf_token,
        settings.auth_csrf_challenge_ttl_seconds,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf_token}


@router.get("/csrf/session")
def get_session_csrf_token(
    request: Request,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
) -> dict[str, str]:
    raw_session_token = request.cookies.get("session", "")
    csrf_token = session_csrf_token(raw_session_token)
    if not hmac.compare_digest(authenticated.session.csrf_token_hash, digest(csrf_token)):
        raise HTTPException(status_code=401, detail="Session must be renewed")
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf_token}


@router.post("/register", status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> dict[str, Any]:
    _require_csrf_challenge(request, response, session)
    if session.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user, recovery_code = create_user(session, body.email, body.password, body.display_name)
    raw_session_token, csrf_token = create_auth_session(session, user)
    add_audit(session, "register", user.id)
    try:
        session.commit()
    except IntegrityError as exception:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        ) from exception

    return _session_response(
        response,
        raw_session_token,
        csrf_token,
        user,
        recovery_code=recovery_code,
    )


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> dict[str, Any]:
    _require_csrf_challenge(request, response, session)
    user = session.scalar(
        select(User).where(User.email == body.email, User.deleted_at.is_(None)).with_for_update()
    )

    if login_is_locked(session, body.email):
        record_failed_login(session, body.email, user)
        raise HTTPException(
            status_code=429, detail="Too many failed login attempts; try again later"
        )

    if not verify_credentials(user, body.password):
        locked = record_failed_login(session, body.email, user)
        if locked:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts; try again later",
            )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    assert user is not None
    reset_failed_logins(session, body.email)
    raw_session_token, csrf_token = create_auth_session(session, user)
    add_audit(session, "login", user.id)
    session.commit()
    return _session_response(response, raw_session_token, csrf_token, user)


@router.post("/logout")
def logout(
    response: Response,
    session: DbSession,
    authenticated: CurrentCsrfAuthenticatedSession,
) -> dict[str, str]:
    authenticated.session.revoked_at = datetime.now(UTC)
    add_audit(session, "logout", authenticated.user.id)
    session.commit()
    clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}


@router.post("/reset-password")
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> dict[str, str]:
    _require_csrf_challenge(request, response, session)
    user = session.scalar(select(User).where(User.email == body.email, User.deleted_at.is_(None)))
    if not verify_recovery_code(user, body.recovery_code):
        add_audit(
            session,
            "password_reset_failed",
            user.id if user is not None else None,
        )
        session.commit()
        raise HTTPException(status_code=400, detail="Invalid email or recovery code")

    assert user is not None
    user.password_hash = password_hash.hash(body.new_password)
    user.recovery_code_hash = None
    user.updated_at = datetime.now(UTC)
    for auth_session in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        auth_session.revoked_at = datetime.now(UTC)
    session.execute(
        delete(LoginAttempt).where(LoginAttempt.email_hash == digest(f"login:{user.email}"))
    )
    add_audit(session, "password_reset", user.id)
    session.commit()
    clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "password_reset"}


@router.get("/me", response_model=AuthUserResponse)
def get_me(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
) -> AuthUserResponse:
    response.headers["Cache-Control"] = "no-store"
    return AuthUserResponse(
        id=str(authenticated.user.id),
        email=authenticated.user.email,
        display_name=authenticated.user.display_name,
    )
