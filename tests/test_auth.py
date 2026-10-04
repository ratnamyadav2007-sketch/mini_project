from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.routes.auth.service import digest
from app.db.models import AuditLog, AuthSession, LoginAttempt, MemberProfile, User

PASSWORD = "StrongPassphrase2026!"
EMAIL = "member@example.com"


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _register(client: TestClient, email: str = EMAIL) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": _csrf(client)},
        json={"email": email, "password": PASSWORD, "display_name": "Test Member"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_hashes_password_and_recovery_code_and_starts_session(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={
            "email": "  MEMBER@Example.Com ",
            "password": PASSWORD,
            "display_name": " Test Member ",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    user = db_session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != PASSWORD
    assert body["recovery_code"]
    assert user.recovery_code_hash != body["recovery_code"]
    assert user.recovery_code_hash == digest(body["recovery_code"])
    assert body["user"]["email"] == EMAIL
    assert body["user"]["display_name"] == "Test Member"
    assert db_session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "secure" not in response.headers["set-cookie"].lower()
    stored_session = db_session.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    assert stored_session is not None
    assert stored_session.token_hash == digest(auth_client.cookies.get("session", ""))
    assert stored_session.csrf_token_hash == digest(body["csrf_token"])
    assert auth_client.get("/api/v1/auth/me").json()["email"] == EMAIL
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "register"))


def test_registration_requires_single_use_csrf_challenge(auth_client: TestClient) -> None:
    token = _csrf(auth_client)
    payload = {"email": EMAIL, "password": PASSWORD, "display_name": "Test Member"}

    missing = auth_client.post("/api/v1/auth/register", json=payload)
    created = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": token},
        json=payload,
    )
    replayed = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": token},
        json=payload,
    )

    assert missing.status_code == 403
    assert created.status_code == 201
    assert replayed.status_code == 403


def test_csrf_challenge_is_bound_to_httponly_cookie(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    challenge = auth_client.get("/api/v1/auth/csrf")
    token = challenge.json()["csrf_token"]
    assert "httponly" in challenge.headers["set-cookie"].lower()
    assert "samesite=lax" in challenge.headers["set-cookie"].lower()
    assert challenge.headers["cache-control"] == "no-store"
    with TestClient(auth_client.app) as other_client:
        response = other_client.post(
            "/api/v1/auth/register",
            headers={"X-CSRF-Token": token},
            json={"email": EMAIL, "password": PASSWORD, "display_name": "Test Member"},
        )

    assert response.status_code == 403
    assert db_session.scalar(select(User).where(User.email == EMAIL)) is None


def test_login_rotates_session_and_logout_revokes_it(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client)
    first_token = auth_client.cookies.get("session")
    assert first_token

    login = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert login.status_code == 200
    second_token = auth_client.cookies.get("session")
    assert second_token and second_token != first_token
    assert auth_client.get("/api/v1/auth/me").json()["email"] == EMAIL

    sessions = list(db_session.scalars(select(AuthSession).where(AuthSession.user_id.is_not(None))))
    assert len(sessions) == 2
    assert sum(item.revoked_at is None for item in sessions) == 1

    csrf_rejected = auth_client.post("/api/v1/auth/logout")
    assert csrf_rejected.status_code == 403
    assert auth_client.get("/api/v1/auth/me").status_code == 200

    logout = auth_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert logout.status_code == 200
    assert logout.json() == {"status": "logged_out"}
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    actions = set(db_session.scalars(select(AuditLog.action)))
    assert {"register", "login", "logout"} <= actions
    assert registration["recovery_code"]


def test_csrf_token_can_be_restored_after_page_reload(auth_client: TestClient) -> None:
    registration = _register(auth_client)

    restored = auth_client.get("/api/v1/auth/csrf/session")

    assert restored.status_code == 200
    assert restored.json() == {"csrf_token": registration["csrf_token"]}
    assert restored.headers["cache-control"] == "no-store"


def test_login_from_another_tab_revokes_the_previous_session(
    auth_client: TestClient,
) -> None:
    _register(auth_client)
    previous_token = auth_client.cookies.get("session")
    assert previous_token

    with TestClient(auth_client.app) as other_tab:
        login = other_tab.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": _csrf(other_tab)},
            json={"email": EMAIL, "password": PASSWORD},
        )

        assert login.status_code == 200
        assert login.cookies.get("session") != previous_token
        assert auth_client.get("/api/v1/auth/me").status_code == 401
        assert other_tab.get("/api/v1/auth/me").status_code == 200


def test_logout_in_another_tab_invalidates_all_tabs(auth_client: TestClient) -> None:
    _register(auth_client)
    session_token = auth_client.cookies.get("session")
    assert session_token

    with TestClient(auth_client.app, cookies={"session": session_token}) as other_tab:
        current_user = other_tab.get("/api/v1/auth/me")
        restored_csrf = other_tab.get("/api/v1/auth/csrf/session")
        assert current_user.status_code == 200
        assert restored_csrf.status_code == 200

        logout = other_tab.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": restored_csrf.json()["csrf_token"]},
        )

    assert logout.status_code == 200
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    _register(auth_client)
    auth_session = db_session.scalar(select(AuthSession))
    assert auth_session is not None
    auth_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_password_reset_consumes_recovery_code_and_invalidates_sessions(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client)
    old_token = auth_client.cookies.get("session")
    new_password = "AnotherStrongPassphrase2026!"
    reset = auth_client.post(
        "/api/v1/auth/reset-password",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={
            "email": EMAIL,
            "recovery_code": registration["recovery_code"],
            "new_password": new_password,
        },
    )

    assert reset.status_code == 200
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    user = db_session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert user.recovery_code_hash is None
    assert all(
        item.revoked_at is not None
        for item in db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))
    )

    old_login = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert old_login.status_code == 401
    new_login = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": new_password},
    )
    assert new_login.status_code == 200
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "password_reset"))
    assert old_token


def test_failed_logins_are_audited_and_temporarily_locked(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    _register(auth_client)

    for attempt in range(5):
        response = auth_client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": _csrf(auth_client)},
            json={"email": EMAIL, "password": "IncorrectPassphrase2026!"},
        )
        assert response.status_code == (429 if attempt == 4 else 401)

    blocked = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert blocked.status_code == 429
    attempts = list(db_session.scalars(select(LoginAttempt)))
    assert len(attempts) == 1
    assert attempts[0].failed_attempts == 5
    assert attempts[0].locked_until is not None
    assert (
        len(list(db_session.scalars(select(AuditLog).where(AuditLog.action == "login_failed"))))
        == 6
    )


def test_login_lockout_expires(auth_client: TestClient, db_session: Session) -> None:
    _register(auth_client)
    for _ in range(5):
        auth_client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": _csrf(auth_client)},
            json={"email": EMAIL, "password": "IncorrectPassphrase2026!"},
        )

    attempt = db_session.scalar(select(LoginAttempt))
    assert attempt is not None
    attempt.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    login = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": PASSWORD},
    )

    assert login.status_code == 200
    assert db_session.scalar(select(LoginAttempt)) is None


def test_reset_recovery_code_is_single_use(auth_client: TestClient) -> None:
    registration = _register(auth_client)
    payload = {
        "email": EMAIL,
        "recovery_code": registration["recovery_code"],
        "new_password": "AnotherStrongPassphrase2026!",
    }

    first = auth_client.post(
        "/api/v1/auth/reset-password",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json=payload,
    )
    second = auth_client.post(
        "/api/v1/auth/reset-password",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 400


def test_registration_rejects_short_password(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": _csrf(auth_client)},
        json={"email": EMAIL, "password": "short", "display_name": "Test Member"},
    )

    assert response.status_code == 422
