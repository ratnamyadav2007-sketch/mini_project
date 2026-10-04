import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app


def request_json(client: TestClient, method: str, path: str, **kwargs):
    response = client.request(method, f"/api/v1/{path.lstrip('/')}", **kwargs)
    if not response.is_success:
        raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text}")
    return response.json() if response.content else None


def main() -> None:
    email = os.environ.get("DEMO_EMAIL", "demo.owner@example.com")
    password = os.environ.get("DEMO_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("Set DEMO_PASSWORD to the password used by backend-seed-demo")
    with TestClient(app) as client:
        challenge = request_json(client, "GET", "/auth/csrf")
        login = request_json(
            client,
            "POST",
            "/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": challenge["csrf_token"]},
        )
        session_csrf = login["csrf_token"]
        headers = {"X-CSRF-Token": session_csrf}
        today = datetime.now(UTC).date()
        profiles = request_json(client, "GET", "/profiles")
        own_profile = next((profile for profile in profiles if profile["user_id"]), None)
        if own_profile is None:
            raise SystemExit("The demo account has no profile; run backend-seed-demo first")
        profile_id = own_profile["id"]
        timeline = request_json(client, "GET", f"/profiles/{profile_id}/timeline?limit=100")
        if not any(
            record["type"] == "systolic_blood_pressure"
            and record["data"].get("name") == "Demo systolic blood pressure"
            for record in timeline["items"]
        ):
            request_json(
                client,
                "POST",
                f"/profiles/{profile_id}/records",
                headers=headers,
                json={
                    "type": "systolic_blood_pressure",
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "visibility": "private",
                    "data": {
                        "name": "Demo systolic blood pressure",
                        "value": 122,
                        "unit": "mmHg",
                    },
                },
            )
        timeline = request_json(client, "GET", f"/profiles/{profile_id}/timeline?limit=100")
        contacts = request_json(client, "GET", f"/profiles/{profile_id}/emergency-contacts")
        if not any(contact["name"] == "Demo Emergency Contact" for contact in contacts):
            request_json(
                client,
                "POST",
                f"/profiles/{profile_id}/emergency-contacts",
                headers=headers,
                json={
                    "name": "Demo Emergency Contact",
                    "relationship": "Family",
                    "phone_number": "+1-555-0100",
                    "priority": 1,
                },
            )
            contacts = request_json(client, "GET", f"/profiles/{profile_id}/emergency-contacts")
        emergency_card = request_json(client, "GET", f"/profiles/{profile_id}/emergency-card")
        appointments = request_json(client, "GET", f"/profiles/{profile_id}/appointments")
        reminders = request_json(client, "GET", f"/profiles/{profile_id}/reminders")
        if not any(reminder["title"] == "Demo hydration reminder" for reminder in reminders):
            request_json(
                client,
                "POST",
                f"/profiles/{profile_id}/reminders",
                headers=headers,
                json={
                    "title": "Demo hydration reminder",
                    "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "message": "A fictitious local demo reminder.",
                    "recurrence": "none",
                },
            )
            reminders = request_json(client, "GET", f"/profiles/{profile_id}/reminders")
        due_reminders = request_json(client, "GET", "/reminders/due")
        goals = request_json(client, "GET", f"/profiles/{profile_id}/goals")
        hydration_goal = next((goal for goal in goals if goal["kind"] == "water"), None)
        if hydration_goal:
            request_json(
                client,
                "POST",
                f"/goals/{hydration_goal['id']}/logs",
                headers=headers,
                json={"value": 1.5, "logged_on": today.isoformat()},
            )
        diet_plans = request_json(client, "GET", f"/profiles/{profile_id}/diet/plans")
        if not any(
            plan["starts_on"] <= today.isoformat() <= plan["ends_on"]
            for plan in diet_plans["items"]
        ):
            request_json(
                client,
                "POST",
                f"/profiles/{profile_id}/diet/plans/generate",
                headers=headers,
                json={
                    "weight_kg": 68,
                    "height_cm": 165,
                    "activity_level": "light",
                    "starts_on": today.isoformat(),
                },
            )
        insight = request_json(
            client,
            "GET",
            f"/profiles/{profile_id}/insights/systolic_blood_pressure",
        )
        chat = request_json(
            client,
            "POST",
            "/chat/messages",
            headers=headers,
            json={"profile_id": profile_id, "message": "Give me a hydration tip."},
        )
        families = request_json(client, "GET", "/families")
        family_dashboard = None
        if families:
            family_dashboard = request_json(
                client,
                "GET",
                f"/families/{families[0]['id']}/dashboard",
            )
        export = request_json(client, "GET", f"/profiles/{profile_id}/export.json")
        request_json(
            client,
            "POST",
            "/auth/logout",
            headers=headers,
        )
        print(f"Authenticated as {email}; visible profiles: {len(profiles)}")
        print(
            f"Timeline records: {len(timeline['items'])}; insight points: {len(insight['points'])}"
        )
        print(
            f"Emergency contacts: {len(contacts)}; card fields: {len(emergency_card)}; "
            f"appointments: {len(appointments)}; reminders: {len(reminders)}"
        )
        print(f"Due reminders delivered: {len(due_reminders)}")
        member_count = len(family_dashboard["members"]) if family_dashboard else 0
        print(f"Family dashboard members: {member_count}")
        print(f"Hydration tip: {chat['answer']}")
        print(f"Exported health records: {len(export['health_records'])}")
        print(
            "Demo completed: health, emergency card setup, appointments, reminders, goals, "
            "diet, insights, chat, family, export, logout."
        )
        print("SOS delivery was intentionally not triggered; the demo uses fictitious local data.")


if __name__ == "__main__":
    main()
