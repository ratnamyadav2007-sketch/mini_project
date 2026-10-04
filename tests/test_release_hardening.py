import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Attachment,
    AuditLog,
    Family,
    FamilyMembership,
    HealthRecord,
    MemberProfile,
    User,
)
from app.db.seed import seed_reference_data
from app.security.permissions import Role
from app.tasks import purge_expired_accounts

PASSWORD = "StrongPassphrase2026!"


@pytest.fixture
def release_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"r" * 32).decode("ascii"))
    monkeypatch.setenv("ATTACHMENT_STORAGE_PATH", str(tmp_path / "attachments"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": _csrf(client)},
        json={"email": email, "password": PASSWORD, "display_name": "Release Test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile_id(session: Session, email: str) -> UUID:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    return profile.id


def test_export_formats_are_authorized_and_non_cacheable(
    auth_client: TestClient,
    db_session: Session,
    release_settings: None,
) -> None:
    registration = _register(auth_client, "export-owner@example.com")
    profile_id = _profile_id(db_session, "export-owner@example.com")
    seed_reference_data(db_session)
    db_session.commit()
    created = auth_client.post(
        f"/api/v1/profiles/{profile_id}/records",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={
            "type": "condition",
            "recorded_at": datetime.now(UTC).isoformat(),
            "data": {"name": "Demo condition"},
        },
    )
    assert created.status_code == 201, created.text

    json_export = auth_client.get(f"/api/v1/profiles/{profile_id}/export.json")
    assert json_export.status_code == 200, json_export.text
    assert json_export.headers["cache-control"] == "no-store"
    assert json_export.json()["health_records"][0]["data"]["name"] == "Demo condition"

    fhir_export = auth_client.get(f"/api/v1/profiles/{profile_id}/export.fhir")
    assert fhir_export.status_code == 200, fhir_export.text
    assert fhir_export.json()["resourceType"] == "Bundle"
    assert fhir_export.json()["entry"][0]["resource"]["resourceType"] == "Patient"

    pdf_export = auth_client.get(f"/api/v1/profiles/{profile_id}/export.pdf")
    assert pdf_export.status_code == 200
    assert pdf_export.headers["content-type"] == "application/pdf"
    assert pdf_export.headers["cache-control"] == "no-store"
    assert pdf_export.content.startswith(b"%PDF-")

    with TestClient(auth_client.app) as stranger:
        _register(stranger, "export-stranger@example.com")
        denied = stranger.get(f"/api/v1/profiles/{profile_id}/export.json")
    assert denied.status_code == 403


def test_export_rate_limit_and_secure_headers(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    _register(auth_client, "limit-owner@example.com")
    profile_id = _profile_id(db_session, "limit-owner@example.com")
    assert auth_client.get("/api/v1/health").headers["x-content-type-options"] == "nosniff"
    assert auth_client.get("/api/v1/health").headers["x-frame-options"] == "DENY"
    assert (
        "default-src 'none'" in auth_client.get("/api/v1/health").headers["content-security-policy"]
    )

    responses = [auth_client.get(f"/api/v1/profiles/{profile_id}/export.json") for _ in range(6)]
    assert [response.status_code for response in responses] == [200] * 5 + [429]
    assert responses[-1].headers["retry-after"]


def test_gzip_and_static_cache_headers(auth_client: TestClient) -> None:
    compressed = auth_client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert compressed.status_code == 200
    assert compressed.headers["content-encoding"] == "gzip"

    html = auth_client.get("/app-shell.html")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-cache"


def test_deletion_waits_for_grace_then_anonymizes_and_removes_files(
    auth_client: TestClient,
    db_session: Session,
    release_settings: None,
) -> None:
    email = "delete-owner@example.com"
    registration = _register(auth_client, email)
    profile_id = _profile_id(db_session, email)
    upload = auth_client.post(
        f"/api/v1/profiles/{profile_id}/attachments",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        files={"file": ("report.pdf", b"%PDF-1.7 demo", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    attachment_id = UUID(upload.json()["id"])
    attachment = db_session.get(Attachment, attachment_id)
    assert attachment is not None
    stored_path = Path(get_settings().attachment_storage_path) / attachment.object_key
    assert stored_path.is_file()

    deletion = auth_client.delete(
        "/api/v1/auth/account",
        headers={"X-CSRF-Token": registration["csrf_token"]},
    )
    assert deletion.status_code == 202, deletion.text
    assert deletion.json()["grace_period_days"] == "30"
    user = db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    purge_after = user.purge_after
    assert purge_after is not None
    assert purge_expired_accounts(db_session, now=purge_after - timedelta(seconds=1)) == 0
    assert stored_path.is_file()

    later = purge_after + timedelta(seconds=1)
    assert (
        db_session.scalar(select(User.id).where(User.id == user.id, User.purge_after <= later))
        == user.id
    )
    assert purge_expired_accounts(db_session, now=later) == 1
    assert not stored_path.exists()
    assert db_session.scalar(select(Attachment.id).where(Attachment.id == attachment_id)) is None
    tombstone = db_session.get(User, user.id)
    assert tombstone is not None
    assert tombstone.email.endswith("@deleted.invalid")
    assert tombstone.display_name == "Deleted account"
    assert tombstone.purge_after is None
    assert (
        db_session.scalar(select(AuditLog.id).where(AuditLog.action == "account_purged"))
        is not None
    )


def test_deletion_requires_guardian_handoff_and_preserves_dependent_key(
    auth_client: TestClient,
    db_session: Session,
    release_settings: None,
) -> None:
    owner_email = "guardian-delete@example.com"
    owner_registration = _register(auth_client, owner_email)
    owner_profile_id = _profile_id(db_session, owner_email)
    owner = db_session.scalar(select(User).where(User.email == owner_email))
    assert owner is not None
    family = Family(name="Custody test family", created_by_user_id=owner.id)
    dependent = MemberProfile(display_name="Dependent", user_id=None)
    db_session.add_all([family, dependent])
    db_session.flush()
    db_session.add_all(
        [
            FamilyMembership(
                family_id=family.id,
                profile_id=owner_profile_id,
                role=Role.GUARDIAN.value,
                status="active",
            ),
            FamilyMembership(
                family_id=family.id,
                profile_id=dependent.id,
                role=Role.DEPENDENT.value,
                status="active",
            ),
        ]
    )
    seed_reference_data(db_session)
    db_session.commit()
    created = auth_client.post(
        f"/api/v1/profiles/{dependent.id}/records",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={
            "type": "condition",
            "recorded_at": datetime.now(UTC).isoformat(),
            "data": {"name": "Retained dependent record"},
        },
    )
    assert created.status_code == 201, created.text
    dependent_record_id = UUID(created.json()["id"])

    no_guardian = auth_client.delete(
        "/api/v1/auth/account",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
    )
    assert no_guardian.status_code == 409
    db_session.refresh(owner)
    assert owner.deleted_at is None

    with TestClient(auth_client.app) as successor_client:
        successor_email = "guardian-successor@example.com"
        _register(successor_client, successor_email)
        successor_profile_id = _profile_id(db_session, successor_email)
        successor = db_session.scalar(select(User).where(User.email == successor_email))
        assert successor is not None
        db_session.add(
            FamilyMembership(
                family_id=family.id,
                profile_id=successor_profile_id,
                role=Role.ADULT.value,
                status="active",
            )
        )
        db_session.commit()

        deletion = auth_client.delete(
            "/api/v1/auth/account",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        )
        assert deletion.status_code == 202, deletion.text
        db_session.refresh(family)
        assert family.created_by_user_id == successor.id
        successor_membership = db_session.scalar(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family.id,
                FamilyMembership.profile_id == successor_profile_id,
            )
        )
        assert successor_membership is not None
        assert successor_membership.role == Role.GUARDIAN.value
        assert (
            successor_client.get(f"/api/v1/records/{dependent_record_id}").json()["data"]["name"]
            == "Retained dependent record"
        )

        owner.purge_after = owner.purge_after.replace(tzinfo=UTC) + timedelta(days=1)
        db_session.commit()
        assert (
            purge_expired_accounts(
                db_session,
                now=owner.purge_after + timedelta(seconds=1),
            )
            == 1
        )
        db_session.refresh(owner)
        assert owner.wrapped_data_key is not None
        assert db_session.get(HealthRecord, dependent_record_id) is not None
        retained = successor_client.get(f"/api/v1/records/{dependent_record_id}")
        assert retained.status_code == 200, retained.text
        assert retained.json()["data"]["name"] == "Retained dependent record"
