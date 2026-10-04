import base64
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MemberProfile, RecordType, ReferenceRange, Reminder, User
from app.db.seed import seed_reference_data
from app.services.chat import EducationTopic, education_answer

PASSWORD = "StrongPassphrase2026!"


@pytest.fixture
def insights_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"i" * 32).decode("ascii"))
    monkeypatch.setenv("NOTIFICATION_LOG_PATH", str(tmp_path / "chat.log"))
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
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile(session: Session, email: str, age: int, sex: str) -> MemberProfile:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    today = date.today()
    try:
        profile.date_of_birth = today.replace(year=today.year - age)
    except ValueError:
        profile.date_of_birth = today.replace(year=today.year - age, day=28)
    profile.sex = sex
    session.commit()
    return profile


def _create_reading(
    client: TestClient,
    csrf: str,
    profile_id: UUID,
    value: int,
    days_ago: int,
    *,
    visibility: str = "private",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/records",
        headers={"X-CSRF-Token": csrf},
        json={
            "type": "systolic_blood_pressure",
            "recorded_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
            "data": {"value": value, "unit": "mmHg"},
            "visibility": visibility,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _client_for_session(session: Session) -> TestClient:
    from app.db.session import get_db
    from app.main import create_app

    application = create_app()

    def override_get_db():
        yield session

    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application)


def test_time_series_flags_dates_trend_and_improvement(
    auth_client: TestClient,
    db_session: Session,
    insights_settings: None,
) -> None:
    registration = _register(auth_client, "series@example.com")
    profile = _profile(db_session, "series@example.com", 35, "female")
    seed_reference_data(db_session)
    record_type = db_session.scalar(
        select(RecordType).where(RecordType.code == "systolic_blood_pressure")
    )
    assert record_type is not None
    _create_reading(auth_client, registration["csrf_token"], profile.id, 150, 14)
    _create_reading(auth_client, registration["csrf_token"], profile.id, 120, 1)
    db_session.add(
        ReferenceRange(
            record_type_id=record_type.id,
            unit="mmHg",
            sex="female",
            age_min=34,
            age_max=36,
            lower_bound=90,
            upper_bound=120,
            population="female age 34-36 test range",
            source_note="Test fixture only.",
        )
    )
    db_session.commit()

    response = auth_client.get(
        f"/api/v1/profiles/{profile.id}/insights/systolic_blood_pressure",
        params={
            "start_date": (date.today() - timedelta(days=7)).isoformat(),
            "end_date": date.today().isoformat(),
            "member_id": str(profile.id),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [point["value"] for point in body["points"]] == [120]
    assert body["points"][0]["range_flag"] == "within"

    full = auth_client.get(
        f"/api/v1/profiles/{profile.id}/insights/systolic_blood_pressure",
        params={"start_date": (date.today() - timedelta(days=30)).isoformat()},
    )
    assert full.status_code == 200, full.text
    assert [point["range_flag"] for point in full.json()["points"]] == ["above", "within"]
    assert full.json()["trend"] == "decreasing"
    assert full.json()["improvement"] == "improved"
    assert full.json()["improvement_delta"] == 1.0
    chart = auth_client.get(
        "/api/v1/insights",
        params={
            "metric": "systolic_blood_pressure",
            "range": "30d",
            "members": str(profile.id),
        },
    )
    assert chart.status_code == 200, chart.text
    assert chart.json()["series"][0]["locked"] is False
    assert [point["value"] for point in chart.json()["series"][0]["points"]] == [150, 120]
    assert chart.json()["series"][0]["points"][0]["reference_lower"] == 90
    assert chart.json()["series"][0]["points"][0]["reference_upper"] == 120
    assert chart.json()["reference_ranges"][0]["population"] == "female age 34-36 test range"


def test_family_comparison_includes_only_consented_members_and_normalizes_demographics(
    auth_client: TestClient,
    db_session: Session,
    insights_settings: None,
) -> None:
    owner_registration = _register(auth_client, "insight.owner@example.com")
    owner_profile = _profile(db_session, "insight.owner@example.com", 35, "female")
    seed_reference_data(db_session)
    family_response = auth_client.post(
        "/api/v1/families",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"name": "Insight Family"},
    )
    assert family_response.status_code == 201, family_response.text
    family_id = UUID(family_response.json()["id"])

    with _client_for_session(db_session) as member_client:
        member_registration = _register(member_client, "insight.member@example.com")
        member_profile = _profile(db_session, "insight.member@example.com", 45, "male")
        invite = auth_client.post(
            f"/api/v1/families/{family_id}/invites",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={"role": "adult"},
        )
        assert invite.status_code == 201, invite.text
        accepted = member_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"invite_code": invite.json()["invite_code"]},
        )
        assert accepted.status_code == 200, accepted.text
        with _client_for_session(db_session) as third_client:
            third_registration = _register(third_client, "insight.third@example.com")
            third_profile = _profile(db_session, "insight.third@example.com", 28, "female")
            third_invite = auth_client.post(
                f"/api/v1/families/{family_id}/invites",
                headers={"X-CSRF-Token": owner_registration["csrf_token"]},
                json={"role": "adult"},
            )
            assert third_invite.status_code == 201, third_invite.text
            third_accepted = third_client.post(
                "/api/v1/family-invites/accept",
                headers={"X-CSRF-Token": third_registration["csrf_token"]},
                json={"invite_code": third_invite.json()["invite_code"]},
            )
            assert third_accepted.status_code == 200, third_accepted.text
            _create_reading(
                third_client,
                third_registration["csrf_token"],
                third_profile.id,
                210,
                0,
                visibility="family",
            )

        consent = member_client.post(
            f"/api/v1/families/{family_id}/consents",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={
                "profile_id": str(member_profile.id),
                "recipient_profile_id": str(owner_profile.id),
                "resources": ["health_records"],
            },
        )
        assert consent.status_code == 201, consent.text

        record_type = db_session.scalar(
            select(RecordType).where(RecordType.code == "systolic_blood_pressure")
        )
        assert record_type is not None
        for sex, age in (("female", 35), ("male", 45), ("female", 28)):
            db_session.add(
                ReferenceRange(
                    record_type_id=record_type.id,
                    unit="mmHg",
                    sex=sex,
                    age_min=age - 1,
                    age_max=age,
                    lower_bound=90,
                    upper_bound=120,
                    population=f"{sex} age {age} test range",
                    source_note="Test fixture only.",
                )
            )
        db_session.commit()

        _create_reading(auth_client, owner_registration["csrf_token"], owner_profile.id, 150, 14)
        _create_reading(auth_client, owner_registration["csrf_token"], owner_profile.id, 120, 1)
        _create_reading(
            member_client,
            member_registration["csrf_token"],
            member_profile.id,
            150,
            14,
            visibility="family",
        )
        _create_reading(
            member_client,
            member_registration["csrf_token"],
            member_profile.id,
            120,
            1,
            visibility="family",
        )
        private_record = _create_reading(
            member_client,
            member_registration["csrf_token"],
            member_profile.id,
            220,
            0,
            visibility="private",
        )

        member_series = auth_client.get(
            f"/api/v1/profiles/{owner_profile.id}/insights/systolic_blood_pressure",
            params={"member_id": str(member_profile.id)},
        )
        assert member_series.status_code == 200, member_series.text
        assert len(member_series.json()["points"]) == 2
        comparison = auth_client.get(
            f"/api/v1/families/{family_id}/insights/compare",
            params={"metric": "systolic_blood_pressure"},
        )
        assert comparison.status_code == 200, comparison.text
        results = comparison.json()["members"]
        assert {item["profile_id"] for item in results} == {
            str(owner_profile.id),
            str(member_profile.id),
        }
        assert all(item["latest_normalized_value"] == 0.5 for item in results)
        assert all(item["improvement_delta"] == 1.0 for item in results)
        assert all("value" not in item for item in results)

        filtered = auth_client.get(
            f"/api/v1/profiles/{owner_profile.id}/insights/systolic_blood_pressure",
            params={"member_id": str(member_profile.id)},
        )
        assert filtered.status_code == 200
        assert [point["value"] for point in filtered.json()["points"]] == [150, 120]
        assert str(private_record["id"]) not in str(filtered.json())
        chart = auth_client.get(
            "/api/v1/insights",
            params={
                "metric": "systolic_blood_pressure",
                "range": "30d",
                "members": ",".join(
                    str(profile_id)
                    for profile_id in (owner_profile.id, member_profile.id, third_profile.id)
                ),
            },
        )
        assert chart.status_code == 200, chart.text
        series = {item["profile_id"]: item for item in chart.json()["series"]}
        assert series[str(owner_profile.id)]["locked"] is False
        assert series[str(member_profile.id)]["locked"] is False
        assert [point["value"] for point in series[str(member_profile.id)]["points"]] == [150, 120]
        assert series[str(third_profile.id)]["locked"] is True
        assert series[str(third_profile.id)]["points"] == []


def test_chat_uses_authorized_tools_prioritizes_emergencies_and_stays_read_only(
    auth_client: TestClient,
    db_session: Session,
    insights_settings: None,
) -> None:
    owner_registration = _register(auth_client, "chat.owner@example.com")
    owner_profile = _profile(db_session, "chat.owner@example.com", 35, "female")
    seed_reference_data(db_session)
    _create_reading(auth_client, owner_registration["csrf_token"], owner_profile.id, 128, 1)
    db_session.add(
        Reminder(
            profile_id=owner_profile.id,
            title="Routine follow-up",
            due_at=datetime.now(UTC) + timedelta(days=1),
            status="pending",
            visibility="private",
        )
    )
    db_session.commit()

    data_response = auth_client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"profile_id": str(owner_profile.id), "message": "What is my blood pressure?"},
    )
    assert data_response.status_code == 200, data_response.text
    assert data_response.json()["intent"] == "data_question"
    assert "128 mmHg" in data_response.json()["answer"]

    reminder_response = auth_client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"profile_id": str(owner_profile.id), "message": "When is my next reminder?"},
    )
    assert reminder_response.status_code == 200, reminder_response.text
    assert reminder_response.json()["actions"][0]["type"] == "reminder"
    assert reminder_response.json()["actions"][0]["href"] == "#/appointments"

    with _client_for_session(db_session) as other_client:
        other_registration = _register(other_client, "chat.other@example.com")
        other_profile = _profile(db_session, "chat.other@example.com", 40, "male")
        _create_reading(other_client, other_registration["csrf_token"], other_profile.id, 220, 0)
        db_session.add(
            Reminder(
                profile_id=other_profile.id,
                title="Private specialist visit",
                due_at=datetime.now(UTC) + timedelta(days=1),
                status="pending",
                visibility="private",
            )
        )
        db_session.commit()
        unauthorized = auth_client.post(
            "/api/v1/chat",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={
                "profile_id": str(other_profile.id),
                "message": "What was my blood pressure?",
            },
        )
        assert unauthorized.status_code == 200
        assert "220" not in unauthorized.json()["answer"]
        hidden_reminder = auth_client.post(
            "/api/v1/chat",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={
                "profile_id": str(other_profile.id),
                "message": "When is my next reminder?",
            },
        )
        assert hidden_reminder.status_code == 200
        assert "Private specialist visit" not in hidden_reminder.json()["answer"]

    emergency = auth_client.post(
        "/api/v1/chat",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={
            "profile_id": str(owner_profile.id),
            "message": "I have chest pain and cannot breathe",
            "allow_model": True,
        },
    )
    assert emergency.status_code == 200
    assert emergency.json()["intent"] == "emergency"
    assert emergency.json()["emergency_guidance"] is True
    assert "cannot contact emergency services" in emergency.json()["answer"]
    assert emergency.json()["actions"][0]["href"] == "#/sos"

    education = auth_client.post(
        "/api/v1/chat",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={
            "profile_id": str(owner_profile.id),
            "message": "Can you explain sleep?",
        },
    )
    assert education.json()["intent"] == "education"
    assert education.json()["source"] == "rule_based"


def test_optional_education_model_receives_only_fixed_topic() -> None:
    class FakeModel:
        received: EducationTopic | None = None

        def answer(self, topic: EducationTopic) -> str | None:
            self.received = topic
            return "A generic educational sentence."

    model = FakeModel()
    answer, fallback = education_answer("sleep", model=model, allow_model=True)
    assert model.received == "sleep"
    assert "generic educational sentence" in answer
    assert "not medical advice" in answer
    assert fallback is False

    class UnsafeModel:
        def answer(self, topic: EducationTopic) -> str | None:
            return "You should take a medication for this."

    answer, fallback = education_answer("sleep", model=UnsafeModel(), allow_model=True)
    assert fallback is True
    assert "medication" not in answer
