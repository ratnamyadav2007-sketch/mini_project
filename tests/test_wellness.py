import base64
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Food, GoalLog, MemberProfile, User
from app.db.seed import seed_reference_data

PASSWORD = "StrongPassphrase2026!"


@pytest.fixture
def wellness_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"w" * 32).decode("ascii"))
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
        json={"email": email, "password": PASSWORD, "display_name": "Wellness User"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile(session: Session, email: str) -> MemberProfile:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    profile.date_of_birth = date.today() - timedelta(days=365 * 35)
    profile.sex = "female"
    session.commit()
    return profile


def _create_health_record(
    client: TestClient,
    csrf: str,
    profile_id: str,
    record_type: str,
    data: dict[str, object],
) -> None:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/records",
        headers={"X-CSRF-Token": csrf},
        json={
            "type": record_type,
            "recorded_at": datetime.now(UTC).isoformat(),
            "data": data,
        },
    )
    assert response.status_code == 201, response.text


def test_generated_week_excludes_allergens_conditions_and_lab_food_exclusions(
    auth_client: TestClient,
    db_session: Session,
    wellness_settings: None,
) -> None:
    registration = _register(auth_client, "diet@example.com")
    profile = _profile(db_session, "diet@example.com")
    seed_reference_data(db_session)
    db_session.add_all(
        [
            Food(
                name="Peanut flour test",
                nutrients_per_100g={"energy_kcal": 500, "protein_g": 20},
                allergens=["peanut"],
                excluded_conditions=[],
                source_note="test fixture",
            ),
            Food(
                name="Almond meal test",
                nutrients_per_100g={"energy_kcal": 500, "protein_g": 20},
                allergens=["almond"],
                excluded_conditions=[],
                source_note="test fixture",
            ),
            Food(
                name="Kidney condition test",
                nutrients_per_100g={"energy_kcal": 200},
                allergens=[],
                excluded_conditions=["kidney disease"],
                source_note="test fixture",
            ),
            Food(
                name="Clinician exclusion test",
                nutrients_per_100g={"energy_kcal": 200},
                allergens=[],
                excluded_conditions=[],
                source_note="test fixture",
            ),
        ]
    )
    db_session.commit()
    csrf = registration["csrf_token"]
    _create_health_record(
        auth_client,
        csrf,
        str(profile.id),
        "allergy",
        {"name": "Peanuts", "severity": "severe"},
    )
    _create_health_record(
        auth_client,
        csrf,
        str(profile.id),
        "allergy",
        {"name": "Tree nuts", "severity": "severe"},
    )
    _create_health_record(
        auth_client,
        csrf,
        str(profile.id),
        "condition",
        {
            "name": "Kidney disease",
            "status": "active",
            "nutrition_targets": {"protein_g": 72},
        },
    )
    _create_health_record(
        auth_client,
        csrf,
        str(profile.id),
        "lab",
        {
            "name": "Clinician note",
            "dietary_exclusions": ["Clinician exclusion test"],
            "nutrition_targets": {"calories_kcal": 2000},
        },
    )

    response = auth_client.post(
        "/api/v1/diet/plans/generate",
        params={"profile_id": str(profile.id)},
        headers={"X-CSRF-Token": csrf},
        json={
            "weight_kg": 68,
            "height_cm": 165,
            "activity_level": "light",
            "starts_on": date.today().isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    meal_names = {meal["name"] for meal in plan["meals"]}
    assert len(plan["meals"]) == 21
    assert plan["target_calories"] > 0
    assert plan["target_calories"] == 2000
    assert plan["macro_targets"]["protein_g"] == 72.0
    assert "Peanut flour test" not in meal_names
    assert "Almond meal test" not in meal_names
    assert "Kidney condition test" not in meal_names
    assert "Clinician exclusion test" not in meal_names
    assert set(plan["excluded_foods"]) == {
        "Peanut flour test",
        "Almond meal test",
        "Kidney condition test",
        "Clinician exclusion test",
    }
    assert plan["disclaimer"]
    assert len(plan["daily_totals"]) == 7
    assert plan["weekly_totals"]["energy_kcal"] > 0
    assert all(isinstance(meal["allergens"], list) for meal in plan["meals"])
    assert all(isinstance(meal["allergen_warnings"], list) for meal in plan["meals"])

    first_meal, second_meal = plan["meals"][:2]
    moved = auth_client.patch(
        f"/api/v1/diet/plans/{plan['id']}/meals/{first_meal['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "scheduled_on": plan["ends_on"],
            "meal_type": "snack",
            "sort_order": 3,
        },
    )
    assert moved.status_code == 200, moved.text
    moved_meal = next(item for item in moved.json()["meals"] if item["id"] == first_meal["id"])
    assert moved_meal["meal_type"] == "snack"
    assert moved_meal["scheduled_on"] == plan["ends_on"]

    swapped = auth_client.post(
        f"/api/v1/diet/plans/{plan['id']}/meals/{first_meal['id']}/swap",
        headers={"X-CSRF-Token": csrf},
        json={"other_meal_id": second_meal["id"]},
    )
    assert swapped.status_code == 200, swapped.text
    items_by_id = {item["id"]: item for item in swapped.json()["meals"]}
    assert items_by_id[first_meal["id"]]["scheduled_on"] == second_meal["scheduled_on"]
    listed = auth_client.get(f"/api/v1/profiles/{profile.id}/diet/plans")
    assert listed.status_code == 200
    assert listed.json()["disclaimer"]
    assert listed.json()["items"][0]["id"] == plan["id"]


def test_daily_goal_logs_are_idempotent_and_award_streak_badges(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client, "goals@example.com")
    profile = _profile(db_session, "goals@example.com")
    csrf = registration["csrf_token"]
    created = auth_client.post(
        f"/api/v1/profiles/{profile.id}/goals",
        headers={"X-CSRF-Token": csrf},
        json={"kind": "steps", "title": "Daily steps", "target_value": 8000},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    today = datetime.now(UTC).date()
    final = None
    for offset in range(6, -1, -1):
        final = auth_client.post(
            f"/api/v1/goals/{goal_id}/logs",
            headers={"X-CSRF-Token": csrf},
            json={"value": 8500, "logged_on": (today - timedelta(days=offset)).isoformat()},
        )
        assert final.status_code == 200, final.text

    assert final is not None
    assert final.json()["goal"]["current_streak"] == 7
    assert final.json()["target_met"] is True
    assert "seven_day_streak" in final.json()["awarded_badges"]
    badge_response = auth_client.get(f"/api/v1/profiles/{profile.id}/badges")
    codes = {item["badge_code"] for item in badge_response.json()}
    assert codes == {"first_goal_log", "goal_target_met", "seven_day_streak"}

    updated = auth_client.post(
        f"/api/v1/goals/{goal_id}/logs",
        headers={"X-CSRF-Token": csrf},
        json={"value": 4000, "logged_on": today.isoformat()},
    )
    assert updated.status_code == 200
    assert updated.json()["goal"]["current_streak"] == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(GoalLog).where(GoalLog.goal_id == UUID(goal_id))
        )
        == 7
    )
    listed = auth_client.get(f"/api/v1/profiles/{profile.id}/goals")
    assert listed.status_code == 200
    assert listed.json()[0]["unit"] == "steps"
    logs = auth_client.get(f"/api/v1/goals/{goal_id}/logs?limit=10")
    assert logs.status_code == 200
    assert len(logs.json()) == 7
    assert logs.json()[0]["value"] == 4000


def test_demo_calorie_targets_use_profile_age_sex_and_activity(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    registration = _register(auth_client, "estimate@example.com")
    profile = _profile(db_session, "estimate@example.com")
    seed_reference_data(db_session)
    db_session.commit()
    today = datetime.now(UTC).date()
    age = (
        today.year
        - profile.date_of_birth.year
        - ((today.month, today.day) < (profile.date_of_birth.month, profile.date_of_birth.day))
    )
    expected = round((10 * 60 + 6.25 * 160 - 5 * age - 161) * 1.2)
    response = auth_client.post(
        f"/api/v1/profiles/{profile.id}/diet/plans/generate",
        headers={"X-CSRF-Token": registration["csrf_token"]},
        json={
            "weight_kg": 60,
            "height_cm": 160,
            "activity_level": "sedentary",
            "starts_on": today.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["target_calories"] == expected
    assert response.json()["macro_targets"]["protein_g"] == 60.0
    assert response.json()["disclaimer"]
