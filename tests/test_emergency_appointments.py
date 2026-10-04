import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    EmergencyContact,
    MemberProfile,
    NotificationOutbox,
    Reminder,
    SOSEvent,
    User,
)
from app.db.seed import seed_reference_data
from app.db.session import get_db
from app.main import create_app
from app.services.notifications import Notifier
from app.tasks import process_due_reminders

PASSWORD = "StrongPassphrase2026!"


@pytest.fixture
def emergency_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"m" * 32).decode("ascii"))
    monkeypatch.setenv("SOS_SIGNING_KEY", "s" * 48)
    monkeypatch.setenv("SOS_LINK_TTL_SECONDS", "300")
    monkeypatch.setenv("SOS_PUBLIC_RATE_LIMIT_PER_MINUTE", "20")
    monkeypatch.setenv("NOTIFICATION_LOG_PATH", str(tmp_path / "notifications.log"))
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
        json={"email": email, "password": PASSWORD, "display_name": "Emergency User"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile(session: Session, email: str) -> MemberProfile:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    return profile


def _independent_client(session: Session) -> TestClient:
    application = create_app()

    def override_get_db():
        yield session

    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application)


def test_emergency_contacts_crud_priority_and_sos_public_health_card(
    auth_client: TestClient,
    db_session: Session,
    emergency_settings: None,
) -> None:
    registration = _register(auth_client, "sos@example.com")
    profile = _profile(db_session, "sos@example.com")
    contact_ids: list[str] = []
    for payload in (
        {
            "name": "Second Contact",
            "relationship": "friend",
            "phone_number": "+15550002",
            "email": "second@example.com",
            "priority": 2,
        },
        {
            "name": "First Contact",
            "relationship": "sibling",
            "phone_number": "+15550001",
            "email": "first@example.com",
            "priority": 1,
        },
    ):
        response = auth_client.post(
            f"/api/v1/profiles/{profile.id}/emergency-contacts",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json=payload,
        )
        assert response.status_code == 201, response.text
        contact_ids.append(response.json()["id"])
    ordered = auth_client.get(f"/api/v1/profiles/{profile.id}/emergency-contacts")
    assert [contact["priority"] for contact in ordered.json()] == [1, 2]
    updated_contact = auth_client.patch(
        f"/api/v1/emergency-contacts/{contact_ids[0]}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"priority": 3},
    )
    assert updated_contact.status_code == 200
    assert updated_contact.json()["priority"] == 3
    assert (
        auth_client.patch(
            f"/api/v1/emergency-contacts/{contact_ids[0]}",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={},
        ).status_code
        == 422
    )

    seed_reference_data(db_session)
    db_session.commit()
    for record_type, data in (
        ("blood_group", {"blood_group": "O+"}),
        ("allergy", {"name": "Peanuts", "reaction": "Anaphylaxis", "severity": "severe"}),
        ("condition", {"name": "Asthma", "status": "active"}),
        ("medication", {"name": "Inhaler", "dosage": "2 puffs", "instructions": "As needed"}),
    ):
        created = auth_client.post(
            f"/api/v1/profiles/{profile.id}/records",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={
                "type": record_type,
                "recorded_at": datetime.now(UTC).isoformat(),
                "data": data,
            },
        )
        assert created.status_code == 201, created.text
    profile_card = auth_client.get(f"/api/v1/profiles/{profile.id}/emergency-card")
    assert profile_card.status_code == 200, profile_card.text
    assert profile_card.json()["name"] == "Emergency User"
    assert profile_card.json()["blood_group"] == "O+"
    assert profile_card.headers["cache-control"] == "no-store"

    triggered = auth_client.post(
        "/api/v1/sos",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={
            "profile_id": str(profile.id),
            "notes": "Do not disclose in public card",
            "location": {"latitude": 12.5, "longitude": -75.2},
        },
    )
    assert triggered.status_code == 201, triggered.text
    body = triggered.json()
    status_response = auth_client.get(f"/api/v1/sos/{body['id']}")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["status"] == "active"
    assert {item["status"] for item in status_response.json()["notifications"]} == {"delivered"}
    assert len(body["notifications"]) == 2
    assert body["public_page_url"].endswith(f"?token={body['public_url'].rsplit('/', 1)[1]}")
    assert {notification["status"] for notification in body["notifications"]} == {"delivered"}
    log_lines = Path(get_settings().notification_log_path).read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 2
    logged = [json.loads(line) for line in log_lines]
    assert all(body["public_page_url"] in line["message"] for line in logged)
    assert all(line["mode"] == "demo" for line in logged)

    token = body["public_url"].rsplit("/", 1)[1]
    with _independent_client(db_session) as public_client:
        card_response = public_client.get(f"/api/v1/sos/{token}")
        assert card_response.status_code == 200, card_response.text
        card = card_response.json()
        assert card["name"] == "Emergency User"
        assert card["blood_group"] == "O+"
        assert card["allergies"][0]["name"] == "Peanuts"
        assert card["allergies"][0]["details"]["reaction"] == "Anaphylaxis"
        assert card["conditions"][0]["name"] == "Asthma"
        assert card["medications"][0]["name"] == "Inhaler"
        assert [contact["priority"] for contact in card["contacts"]] == [1, 3]
        serialized = json.dumps(card)
        assert "Do not disclose in public card" not in serialized
        assert "latitude" not in serialized
        assert card_response.headers["cache-control"] == "no-store"
        assert card_response.headers["referrer-policy"] == "no-referrer"
        assert public_client.get("/api/v1/sos/not-a-signed-token").status_code == 404

    event = db_session.get(SOSEvent, UUID(body["id"]))
    assert event is not None and event.public_token_hash is not None
    event.public_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    with _independent_client(db_session) as public_client:
        assert public_client.get(f"/api/v1/sos/{token}").status_code == 404

    deleted = auth_client.delete(
        f"/api/v1/emergency-contacts/{contact_ids[1]}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
    )
    assert deleted.status_code == 204
    assert db_session.get(EmergencyContact, UUID(contact_ids[1])).deleted_at is not None


def test_sos_link_resolves_and_public_endpoint_rate_limits(
    auth_client: TestClient,
    db_session: Session,
    emergency_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _register(auth_client, "resolve@example.com")
    profile = _profile(db_session, "resolve@example.com")
    triggered = auth_client.post(
        "/api/v1/sos",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"profile_id": str(profile.id)},
    )
    assert triggered.status_code == 201, triggered.text
    token = triggered.json()["public_url"].rsplit("/", 1)[1]

    monkeypatch.setenv("SOS_PUBLIC_RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    with _independent_client(db_session) as public_client:
        assert public_client.get(f"/api/v1/sos/{token}").status_code == 200
        assert public_client.get(f"/api/v1/sos/{token}").status_code == 200
        assert public_client.get(f"/api/v1/sos/{token}").status_code == 429

    resolved = auth_client.post(
        f"/api/v1/sos/{triggered.json()['id']}/resolve",
        headers={"X-CSRF-Token": registration["csrf_token"]},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    monkeypatch.setenv("SOS_PUBLIC_RATE_LIMIT_PER_MINUTE", "20")
    get_settings.cache_clear()
    with _independent_client(db_session) as public_client:
        response = public_client.get(f"/api/v1/sos/{token}")
        assert response.status_code == 404


def test_appointments_ics_and_reminder_crud(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client, "calendar@example.com")
    profile = _profile(db_session, "calendar@example.com")
    starts = datetime.now(UTC) + timedelta(days=2)
    appointment = auth_client.post(
        f"/api/v1/profiles/{profile.id}/appointments",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={
            "title": "Clinic, follow-up",
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(minutes=30)).isoformat(),
            "location": "Room 2; East",
            "notes": "Line one\nBEGIN:VEVENT",
        },
    )
    assert appointment.status_code == 201, appointment.text
    appointment_id = appointment.json()["id"]
    listing = auth_client.get(f"/api/v1/profiles/{profile.id}/appointments")
    assert [item["id"] for item in listing.json()] == [appointment_id]
    calendar_listing = auth_client.get(
        "/api/v1/appointments",
        params={
            "profile_id": str(profile.id),
            "from": (starts - timedelta(minutes=1)).isoformat(),
            "to": (starts + timedelta(minutes=1)).isoformat(),
        },
    )
    assert calendar_listing.status_code == 200, calendar_listing.text
    assert [item["id"] for item in calendar_listing.json()] == [appointment_id]
    calendar_feed = auth_client.get(f"/api/v1/profiles/{profile.id}/appointments.ics")
    assert calendar_feed.status_code == 200
    assert calendar_feed.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Clinic\\, follow-up" in calendar_feed.text
    assert "LOCATION:Room 2\\; East" in calendar_feed.text
    assert "DESCRIPTION:Line one\\nBEGIN:VEVENT" in calendar_feed.text

    updated = auth_client.patch(
        f"/api/v1/appointments/{appointment_id}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"status": "completed", "notes": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "completed"
    assert updated.json()["notes"] is None
    invalid_range = auth_client.patch(
        f"/api/v1/appointments/{appointment_id}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"starts_at": (starts + timedelta(hours=1)).isoformat()},
    )
    assert invalid_range.status_code == 422
    invalid_update = auth_client.patch(
        f"/api/v1/appointments/{appointment_id}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"visibility": None},
    )
    assert invalid_update.status_code == 422

    reminder = auth_client.post(
        f"/api/v1/profiles/{profile.id}/reminders",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={
            "title": "Medication",
            "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "appointment_id": appointment_id,
            "recurrence": "weekly",
            "recurrence_interval": 2,
        },
    )
    assert reminder.status_code == 201, reminder.text
    reminder_id = reminder.json()["id"]
    assert (
        auth_client.patch(
            f"/api/v1/reminders/{reminder_id}",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={"recurrence": None},
        ).status_code
        == 422
    )
    sent_reminder = db_session.get(Reminder, UUID(reminder_id))
    assert sent_reminder is not None
    sent_reminder.status = "sent"
    db_session.commit()
    snoozed = auth_client.post(
        f"/api/v1/reminders/{reminder_id}/snooze",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={"minutes": 30},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["snoozed_until"] is not None
    assert snoozed.json()["status"] == "pending"
    completed = auth_client.post(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers={"X-CSRF-Token": registration["csrf_token"]},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    deleted = auth_client.delete(
        f"/api/v1/appointments/{appointment_id}",
        headers={"X-CSRF-Token": registration["csrf_token"]},
    )
    assert deleted.status_code == 204


def test_due_reminder_polling_returns_only_the_signed_in_users_notifications(
    auth_client: TestClient,
    db_session: Session,
    emergency_settings: None,
) -> None:
    _register(auth_client, "due-reminder@example.com")
    profile = _profile(db_session, "due-reminder@example.com")
    due_at = datetime.now(UTC) - timedelta(seconds=10)
    reminder = Reminder(
        profile_id=profile.id,
        title="Take medication",
        due_at=due_at,
        message="Take the morning dose",
    )
    db_session.add(reminder)
    db_session.commit()

    response = auth_client.get("/api/v1/reminders/due")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "notification_id": None,
            "reminder_id": str(reminder.id),
            "profile_id": str(profile.id),
            "title": "Take medication",
            "message": "Take the morning dose",
            "due_at": due_at.isoformat().replace("+00:00", "Z"),
            "delivered_at": None,
            "notification_status": "pending",
        }
    ]

    with _independent_client(db_session) as other_client:
        other_registration = _register(other_client, "other-due-reminder@example.com")
        assert other_registration["user"]["email"] == "other-due-reminder@example.com"
        assert other_client.get("/api/v1/reminders/due").json() == []

    generated = process_due_reminders(db_session, now=datetime.now(UTC) + timedelta(seconds=1))
    assert generated == 1
    delivered = auth_client.get(
        "/api/v1/reminders/due",
        params={"since": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
    )
    assert delivered.status_code == 200, delivered.text
    assert len(delivered.json()) == 1
    assert delivered.json()[0]["notification_status"] == "delivered"
    assert delivered.json()[0]["message"] == "Take the morning dose"


def test_due_reminder_scheduler_delivers_once_and_advances_recurrence(
    db_session: Session,
    emergency_settings: None,
) -> None:
    user = User(
        email="reminder-owner@example.com",
        password_hash="unused",
        display_name="Reminder owner",
    )
    db_session.add(user)
    db_session.flush()
    profile = MemberProfile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()
    now = datetime(2024, 3, 1, 12, tzinfo=UTC)
    one_shot = Reminder(
        profile_id=profile.id,
        title="One shot",
        due_at=now - timedelta(minutes=5),
        recurrence="none",
    )
    monthly = Reminder(
        profile_id=profile.id,
        title="Monthly",
        due_at=datetime(2024, 1, 31, 9, tzinfo=UTC),
        recurrence="monthly",
        recurrence_interval=1,
        recurrence_anchor_day=31,
    )
    snoozed = Reminder(
        profile_id=profile.id,
        title="Snoozed",
        due_at=now - timedelta(days=1),
        recurrence="none",
        snoozed_until=now + timedelta(minutes=15),
    )
    db_session.add_all([one_shot, monthly, snoozed])
    db_session.commit()

    class CaptureNotifier(Notifier):
        def __init__(self) -> None:
            self.deliveries: list[tuple[str, str]] = []

        def deliver(self, recipient: str, message: str) -> None:
            self.deliveries.append((recipient, message))

    notifier = CaptureNotifier()
    generated = process_due_reminders(db_session, now=now, notifier=notifier)
    assert generated == 2
    assert len(notifier.deliveries) == 2
    assert all(recipient == user.email for recipient, _ in notifier.deliveries)
    db_session.refresh(one_shot)
    db_session.refresh(monthly)
    db_session.refresh(snoozed)
    assert one_shot.status == "sent"
    assert one_shot.last_notified_at.replace(tzinfo=UTC) == now
    assert monthly.status == "pending"
    assert monthly.due_at.replace(tzinfo=UTC) == datetime(2024, 3, 31, 9, tzinfo=UTC)
    assert snoozed.last_notified_at is None
    assert db_session.scalar(
        select(NotificationOutbox).where(NotificationOutbox.status == "delivered")
    )
    assert process_due_reminders(db_session, now=now, notifier=notifier) == 0
    assert len(notifier.deliveries) == 2

    from app.celery_app import celery_app

    assert "process-due-reminders-every-minute" in celery_app.conf.beat_schedule
