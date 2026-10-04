import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Attachment,
    HealthRecord,
    MemberProfile,
    ShareGrant,
    User,
)
from app.db.seed import seed_reference_data

PASSWORD = "StrongPassphrase2026!"


@pytest.fixture
def encryption_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode("ascii"))
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
        json={"email": email, "password": PASSWORD, "display_name": "Test Member"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": _csrf(client)},
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _profile_id(session: Session, email: str) -> UUID:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    return profile.id


def _create_record(
    client: TestClient,
    csrf_token: str,
    profile_id: UUID,
    *,
    record_type: str,
    recorded_at: str,
    data: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/records",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "type": record_type,
            "recorded_at": recorded_at,
            "data": data,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_guardian_can_create_and_list_dependent_profile(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
) -> None:
    registration = _register(auth_client, "guardian@example.com")
    guardian_profile_id = _profile_id(db_session, "guardian@example.com")
    family_response = auth_client.post(
        "/api/v1/families",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"name": "Test family"},
    )
    assert family_response.status_code == 201, family_response.text
    family_id = family_response.json()["id"]

    created = auth_client.post(
        "/api/v1/profiles",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"display_name": "Child", "family_id": family_id},
    )
    assert created.status_code == 201, created.text
    dependent = created.json()
    assert dependent["user_id"] is None

    listed = auth_client.get("/api/v1/profiles")
    assert listed.status_code == 200, listed.text
    assert {profile["id"] for profile in listed.json()} == {
        str(guardian_profile_id),
        dependent["id"],
    }
    fetched = auth_client.get(f"/api/v1/profiles/{dependent['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["display_name"] == "Child"
    unlinked = auth_client.post(
        "/api/v1/profiles",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"display_name": "Unlinked", "family_id": str(uuid4())},
    )
    assert unlinked.status_code == 403


def test_onboarding_profile_updates_and_family_owner_can_add_dependent(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client, "onboarding@example.com")
    own_profile_id = _profile_id(db_session, "onboarding@example.com")
    csrf = registration["csrf_token"]

    saved_step = auth_client.patch(
        f"/api/v1/profiles/{own_profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "display_name": "Sam Example",
            "relationship": "you",
            "date_of_birth": "1992-04-12",
            "blood_group": "O+",
            "allergies": ["Pollen"],
            "avatar_color": "#416c78",
            "avatar_wash": "#e3edef",
            "onboarding_step": 2,
        },
    )
    assert saved_step.status_code == 200, saved_step.text
    assert saved_step.json()["display_name"] == "Sam Example"
    assert saved_step.json()["onboarding_step"] == 2
    assert saved_step.json()["allergies"] == ["Pollen"]
    assert saved_step.json()["avatar_color"] == "#416c78"

    family = auth_client.post(
        "/api/v1/families",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Example family"},
    )
    assert family.status_code == 201, family.text
    dependent = auth_client.post(
        "/api/v1/profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "display_name": "Alex Example",
            "family_id": family.json()["id"],
            "relationship": "child",
            "onboarding_step": 1,
            "avatar_color": "#806016",
        },
    )
    assert dependent.status_code == 201, dependent.text
    dependent_id = dependent.json()["id"]
    assert dependent.json()["relationship"] == "child"

    completed = auth_client.patch(
        f"/api/v1/profiles/{dependent_id}",
        headers={"X-CSRF-Token": csrf},
        json={"onboarding_step": 2, "onboarding_complete": True},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["onboarding_complete"] is True
    listed = auth_client.get("/api/v1/profiles")
    assert {profile["id"] for profile in listed.json()} == {str(own_profile_id), dependent_id}


def test_encrypted_record_crud_timeline_and_visibility(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
) -> None:
    registration = _register(auth_client, "records@example.com")
    profile_id = _profile_id(db_session, "records@example.com")
    seed_reference_data(db_session)
    db_session.commit()
    csrf = registration["csrf_token"]
    first = _create_record(
        auth_client,
        csrf,
        profile_id,
        record_type="condition",
        recorded_at="2020-06-15T12:00:00Z",
        data={"name": "Asthma", "notes": "Sensitive note"},
    )
    _create_record(
        auth_client,
        csrf,
        profile_id,
        record_type="allergy",
        recorded_at="2024-02-01T12:00:00Z",
        data={"name": "Pollen"},
    )
    _create_record(
        auth_client,
        csrf,
        profile_id,
        record_type="condition",
        recorded_at="2022-03-01T12:00:00Z",
        data={"name": "Eczema"},
    )

    stored = db_session.get(HealthRecord, UUID(first["id"]))
    assert stored is not None
    assert stored.value is stored.unit is stored.notes is stored.source is None
    assert stored.encrypted_payload is not None
    assert b"Sensitive note" not in stored.encrypted_payload

    page = auth_client.get(f"/api/v1/profiles/{profile_id}/timeline?type=condition&limit=1")
    assert page.status_code == 200, page.text
    assert len(page.json()["items"]) == 1
    assert page.json()["has_more"] is True
    assert page.json()["items"][0]["data"]["name"] == "Eczema"

    filtered = auth_client.get(
        f"/api/v1/profiles/{profile_id}/timeline?start_year=2020&end_year=2021&search=asthma"
    )
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["items"]] == [first["id"]]

    updated = auth_client.patch(
        f"/api/v1/records/{first['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"data": {"name": "Asthma", "notes": "Updated"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["notes"] == "Updated"
    deleted = auth_client.delete(
        f"/api/v1/records/{first['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 204
    assert auth_client.get(f"/api/v1/records/{first['id']}").status_code == 404


def test_record_type_metadata_and_cursor_pagination(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
) -> None:
    registration = _register(auth_client, "timeline-cursor@example.com")
    profile_id = _profile_id(db_session, "timeline-cursor@example.com")
    seed_reference_data(db_session)
    db_session.commit()

    schemas = auth_client.get("/api/v1/meta/record-types")
    assert schemas.status_code == 200
    condition = next(schema for schema in schemas.json() if schema["code"] == "condition")
    assert {field["name"] for field in condition["fields"]} == {"name", "notes", "status"}
    assert (
        next(field for field in condition["fields"] if field["name"] == "name")["required"] is True
    )

    csrf = registration["csrf_token"]
    records = [
        _create_record(
            auth_client,
            csrf,
            profile_id,
            record_type="condition",
            recorded_at=f"202{year}-01-01T12:00:00Z",
            data={"title": f"Condition {year}", "name": f"Condition {year}"},
        )
        for year in (2, 3, 4)
    ]
    first_page = auth_client.get(f"/api/v1/profiles/{profile_id}/timeline?limit=1")
    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["items"][0]["id"] == records[-1]["id"]
    assert first_page.json()["has_more"] is True

    cursor = first_page.json()["next_cursor"]
    second_page = auth_client.get(f"/api/v1/profiles/{profile_id}/timeline?limit=1&cursor={cursor}")
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["items"][0]["id"] == records[-2]["id"]
    assert second_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]

    invalid_cursor = auth_client.get(
        f"/api/v1/profiles/{profile_id}/timeline?cursor=not-a-valid-cursor"
    )
    assert invalid_cursor.status_code == 422


def test_timeline_first_page_handles_more_than_one_thousand_records(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
) -> None:
    _register(auth_client, "timeline-scale@example.com")
    profile_id = _profile_id(db_session, "timeline-scale@example.com")
    seed_reference_data(db_session)
    db_session.add_all(
        [
            HealthRecord(
                profile_id=profile_id,
                type="condition",
                recorded_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
                notes=f"Seeded record {index}",
            )
            for index in range(1001)
        ]
    )
    db_session.commit()

    started = perf_counter()
    response = auth_client.get(f"/api/v1/profiles/{profile_id}/timeline?limit=100")
    elapsed = perf_counter() - started

    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 100
    assert response.json()["has_more"] is True
    assert response.json()["next_cursor"]
    assert elapsed < 2.0, f"Loading the first 100 of 1,001 records took {elapsed:.3f}s"


def test_record_sharing_requires_active_grant_and_revoke(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
) -> None:
    owner_email = "owner@example.com"
    recipient_email = "recipient@example.com"
    owner_registration = _register(auth_client, owner_email)
    profile_id = _profile_id(db_session, owner_email)
    seed_reference_data(db_session)
    db_session.commit()
    record = _create_record(
        auth_client,
        owner_registration["csrf_token"],
        profile_id,
        record_type="visit_note",
        recorded_at="2024-01-01T00:00:00Z",
        data={"summary": "Shared visit"},
    )
    _create_record(
        auth_client,
        owner_registration["csrf_token"],
        profile_id,
        record_type="condition",
        recorded_at="2023-01-01T00:00:00Z",
        data={"name": "Private condition"},
    )
    _register(auth_client, recipient_email)
    owner_session = _login(auth_client, owner_email)
    shared = auth_client.post(
        f"/api/v1/records/{record['id']}/share",
        headers={"X-CSRF-Token": owner_session["csrf_token"]},
        json={
            "email": recipient_email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert shared.status_code == 201, shared.text

    recipient_session = _login(auth_client, recipient_email)
    read = auth_client.get(f"/api/v1/records/{record['id']}")
    assert read.status_code == 200, read.text
    assert read.json()["data"]["summary"] == "Shared visit"
    timeline = auth_client.get(f"/api/v1/profiles/{profile_id}/timeline")
    assert timeline.status_code == 200
    assert [item["id"] for item in timeline.json()["items"]] == [record["id"]]

    grant = db_session.get(ShareGrant, UUID(shared.json()["id"]))
    assert grant is not None
    grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert auth_client.get(f"/api/v1/records/{record['id']}").status_code == 403
    grant.expires_at = datetime.now(UTC) + timedelta(days=1)
    db_session.commit()

    denied_write = auth_client.patch(
        f"/api/v1/records/{record['id']}",
        headers={"X-CSRF-Token": recipient_session["csrf_token"]},
        json={"data": {"summary": "Tampered"}},
    )
    assert denied_write.status_code == 403

    owner_session = _login(auth_client, owner_email)
    revoked = auth_client.delete(
        f"/api/v1/records/{record['id']}/share/{shared.json()['id']}",
        headers={"X-CSRF-Token": owner_session["csrf_token"]},
    )
    assert revoked.status_code == 204
    _login(auth_client, recipient_email)
    assert auth_client.get(f"/api/v1/records/{record['id']}").status_code == 403


def test_attachment_upload_is_encrypted_validated_and_downloadable(
    auth_client: TestClient,
    db_session: Session,
    encryption_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _register(auth_client, "files@example.com")
    profile_id = _profile_id(db_session, "files@example.com")
    content = b"%PDF-1.7\nSensitive attachment body"
    seed_reference_data(db_session)
    db_session.commit()
    record = _create_record(
        auth_client,
        registration["csrf_token"],
        profile_id,
        record_type="visit_note",
        recorded_at="2026-01-01T12:00:00Z",
        data={"title": "Visit note", "summary": "Linked attachment"},
    )
    uploaded = auth_client.post(
        f"/api/v1/profiles/{profile_id}/attachments",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        data={"record_id": record["id"]},
        files={"file": ("../../private.pdf", content, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_body = uploaded.json()
    assert attachment_body["original_filename"] == "private.pdf"
    attachment = db_session.get(Attachment, UUID(attachment_body["id"]))
    assert attachment is not None
    assert attachment.record_id == UUID(record["id"])
    assert attachment_body["record_id"] == record["id"]
    root = Path(get_settings().attachment_storage_path)
    encrypted_bytes = (root / attachment.object_key).read_bytes()
    assert encrypted_bytes != content
    assert content not in encrypted_bytes

    downloaded = auth_client.get(f"/api/v1/attachments/{attachment.id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["x-content-type-options"] == "nosniff"

    rejected = auth_client.post(
        f"/api/v1/profiles/{profile_id}/attachments",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        files={"file": ("spoofed.pdf", b"not a PDF", "application/pdf")},
    )
    assert rejected.status_code == 415

    monkeypatch.setenv("ATTACHMENT_MAX_SIZE_BYTES", "8")
    get_settings.cache_clear()
    oversized = auth_client.post(
        f"/api/v1/profiles/{profile_id}/attachments",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        files={"file": ("large.pdf", content, "application/pdf")},
    )
    assert oversized.status_code == 413
    get_settings.cache_clear()
