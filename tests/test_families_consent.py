import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Appointment,
    AuditLog,
    Family,
    FamilyInvite,
    Goal,
    HealthRecord,
    MemberProfile,
    RecordType,
    ShareGrant,
    User,
)
from app.db.seed import seed_reference_data
from app.db.session import get_db
from app.main import create_app
from app.security.permissions import Role, Visibility

PASSWORD = "StrongPassphrase2026!"


def _new_client(db_session: Session) -> TestClient:
    application = create_app()

    def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application)


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


def _profile(session: Session, email: str) -> MemberProfile:
    user = session.scalar(select(User).where(User.email == email))
    assert user is not None
    profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert profile is not None
    return profile


def test_family_invite_accept_decline_roles_and_remove_member(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    owner_email = "family.owner@example.com"
    member_email = "family.member@example.com"
    owner_registration = _register(auth_client, owner_email)
    family_response = auth_client.post(
        "/api/v1/families",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"name": "The Example Family"},
    )
    assert family_response.status_code == 201, family_response.text
    family_id = family_response.json()["id"]

    invitation = auth_client.post(
        f"/api/v1/families/{family_id}/invites",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"role": "dependent", "expires_in_hours": 48},
    )
    assert invitation.status_code == 201, invitation.text
    invite_code = invitation.json()["invite_code"]
    stored_invite_hash = db_session.scalar(
        select(FamilyInvite.invite_code_hash).where(
            FamilyInvite.id == UUID(invitation.json()["id"])
        )
    )
    assert stored_invite_hash == hashlib.sha256(invite_code.encode("ascii")).hexdigest()
    assert invite_code != stored_invite_hash
    invite_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "family_invite_created",
            AuditLog.entity_id == UUID(invitation.json()["id"]),
        )
    )
    assert invite_audit is not None

    with _new_client(db_session) as member_client:
        member_registration = _register(member_client, member_email)
        accepted = member_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"invite_code": invite_code},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == Role.DEPENDENT.value
        unauthorized_invite = member_client.post(
            f"/api/v1/families/{family_id}/invites",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"role": "adult"},
        )
        assert unauthorized_invite.status_code == 403
        repeated = member_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"invite_code": invite_code},
        )
        assert repeated.status_code == 409

        member_profile = _profile(db_session, member_email)
        changed = auth_client.patch(
            f"/api/v1/families/{family_id}/members/{member_profile.id}",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={"role": "guardian"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["role"] == Role.GUARDIAN.value
        child = member_client.post(
            "/api/v1/profiles",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"display_name": "Child profile", "family_id": family_id},
        )
        assert child.status_code == 201, child.text
        assert child.json()["user_id"] is None
        guardian_profiles = member_client.get("/api/v1/profiles")
        assert guardian_profiles.status_code == 200
        assert child.json()["id"] in {item["id"] for item in guardian_profiles.json()}

        next_invite = auth_client.post(
            f"/api/v1/families/{family_id}/invites",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={"role": "adult"},
        )
        assert next_invite.status_code == 201
        declined = member_client.post(
            "/api/v1/family-invites/decline",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"invite_code": next_invite.json()["invite_code"]},
        )
        assert declined.status_code == 204
        declined_accept = auth_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={"invite_code": next_invite.json()["invite_code"]},
        )
        assert declined_accept.status_code == 409

        removed = auth_client.delete(
            f"/api/v1/families/{family_id}/members/{member_profile.id}",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        )
        assert removed.status_code == 204
        assert member_client.get(f"/api/v1/families/{family_id}/members").status_code == 403
        members = auth_client.get(f"/api/v1/families/{family_id}/members")
        assert members.status_code == 200
        assert {member["profile_id"] for member in members.json()} == {
            str(_profile(db_session, owner_email).id),
            child.json()["id"],
        }

        expiring = auth_client.post(
            f"/api/v1/families/{family_id}/invites",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
            json={"role": "adult"},
        )
        assert expiring.status_code == 201
        stored_expiring = db_session.get(FamilyInvite, UUID(expiring.json()["id"]))
        assert stored_expiring is not None
        stored_expiring.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
        expired_accept = member_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": member_registration["csrf_token"]},
            json={"invite_code": expiring.json()["invite_code"]},
        )
        assert expired_accept.status_code == 410

        owner_leaves = auth_client.delete(
            f"/api/v1/families/{family_id}/members/{_profile(db_session, owner_email).id}",
            headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        )
        assert owner_leaves.status_code == 204
        stored_family = db_session.get(Family, UUID(family_id))
        assert stored_family is not None and stored_family.deleted_at is not None


def test_family_consent_controls_records_dashboard_and_revoke(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    owner_email = "consent.owner@example.com"
    recipient_email = "consent.recipient@example.com"
    owner_registration = _register(auth_client, owner_email)
    family = auth_client.post(
        "/api/v1/families",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"name": "Consent Family"},
    )
    assert family.status_code == 201, family.text
    family_id = family.json()["id"]

    invite = auth_client.post(
        f"/api/v1/families/{family_id}/invites",
        headers={"X-CSRF-Token": owner_registration["csrf_token"]},
        json={"role": "adult"},
    )
    assert invite.status_code == 201, invite.text
    with _new_client(db_session) as recipient_client:
        recipient_registration = _register(recipient_client, recipient_email)
        accepted = recipient_client.post(
            "/api/v1/family-invites/accept",
            headers={"X-CSRF-Token": recipient_registration["csrf_token"]},
            json={"invite_code": invite.json()["invite_code"]},
        )
        assert accepted.status_code == 200, accepted.text
        owner_profile = _profile(db_session, owner_email)
        recipient_profile = _profile(db_session, recipient_email)

        seed_reference_data(db_session)
        record_type = db_session.scalar(select(RecordType).where(RecordType.code == "condition"))
        assert record_type is not None
        shared_record = HealthRecord(
            profile_id=recipient_profile.id,
            type="condition",
            recorded_at=datetime.now(UTC) - timedelta(days=1),
            visibility=Visibility.FAMILY.value,
            notes="Consented update",
        )
        private_record = HealthRecord(
            profile_id=recipient_profile.id,
            type="condition",
            recorded_at=datetime.now(UTC),
            visibility=Visibility.PRIVATE.value,
            notes="Not consented",
        )
        appointment = Appointment(
            profile_id=recipient_profile.id,
            title="Shared appointment",
            starts_at=datetime.now(UTC) + timedelta(days=1),
            visibility=Visibility.FAMILY.value,
        )
        goal = Goal(
            profile_id=recipient_profile.id,
            title="Shared goal",
            status="active",
            visibility=Visibility.FAMILY.value,
        )
        db_session.add_all([shared_record, private_record, appointment, goal])
        db_session.commit()

        denied = auth_client.get(f"/api/v1/records/{shared_record.id}")
        assert denied.status_code == 403
        before_consent = auth_client.get(f"/api/v1/families/{family_id}/dashboard")
        assert before_consent.status_code == 200
        before_body = before_consent.json()
        assert before_body["recent_updates"] == []
        assert before_body["upcoming_appointments"] == []
        assert before_body["goal_summary"] == []
        assert {member["profile_id"] for member in before_body["members"]} == {
            str(owner_profile.id),
            str(recipient_profile.id),
        }
        assert before_body["family_id"] == family_id
        assert {member["profile_id"]: member["email"] for member in before_body["members"]}[
            str(recipient_profile.id)
        ] == recipient_email

        consent_response = recipient_client.post(
            f"/api/v1/families/{family_id}/consents",
            headers={"X-CSRF-Token": recipient_registration["csrf_token"]},
            json={
                "profile_id": str(recipient_profile.id),
                "recipient_profile_id": str(owner_profile.id),
                "resources": ["health_records", "appointments", "goals"],
            },
        )
        assert consent_response.status_code == 201, consent_response.text
        assert consent_response.json()["resources"] == [
            "health_records",
            "appointments",
            "goals",
        ]
        consent_audit = db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == "family_consent_granted",
                AuditLog.entity_id == UUID(consent_response.json()["id"]),
            )
        )
        assert consent_audit is not None

        consent_grant = db_session.get(ShareGrant, UUID(consent_response.json()["id"]))
        assert consent_grant is not None
        consent_grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
        assert auth_client.get(f"/api/v1/records/{shared_record.id}").status_code == 403
        consent_grant.expires_at = datetime.now(UTC) + timedelta(days=1)
        db_session.commit()

        consented = auth_client.get(f"/api/v1/records/{shared_record.id}")
        assert consented.status_code == 200
        still_private = auth_client.get(f"/api/v1/records/{private_record.id}")
        assert still_private.status_code == 403
        dashboard = auth_client.get(f"/api/v1/families/{family_id}/dashboard")
        assert dashboard.status_code == 200, dashboard.text
        body = dashboard.json()
        assert [row["id"] for row in body["recent_updates"]] == [str(shared_record.id)]
        assert set(body["recent_updates"][0]) == {
            "id",
            "profile_id",
            "type",
            "recorded_at",
            "visibility",
        }
        assert [row["id"] for row in body["upcoming_appointments"]] == [str(appointment.id)]
        assert body["goal_summary"] == [
            {"profile_id": str(recipient_profile.id), "active": 1, "completed": 0, "other": 0}
        ]
        assert len(body["consents"]) == 1
        assert body["consents"][0]["recipient_profile_id"] == str(owner_profile.id)

        partially_revoked = recipient_client.patch(
            f"/api/v1/families/{family_id}/consents/{consent_response.json()['id']}",
            headers={"X-CSRF-Token": recipient_registration["csrf_token"]},
            json={"resources": ["health_records", "appointments"]},
        )
        assert partially_revoked.status_code == 200, partially_revoked.text
        assert partially_revoked.json()["resources"] == ["health_records", "appointments"]
        after_partial_revoke = auth_client.get(f"/api/v1/families/{family_id}/dashboard")
        assert after_partial_revoke.status_code == 200
        assert after_partial_revoke.json()["goal_summary"] == []
        assert [row["id"] for row in after_partial_revoke.json()["recent_updates"]] == [
            str(shared_record.id)
        ]

        consents = auth_client.get(f"/api/v1/families/{family_id}/consents")
        assert consents.status_code == 200
        assert len(consents.json()) == 1
        revoked = recipient_client.delete(
            f"/api/v1/families/{family_id}/consents/{consent_response.json()['id']}",
            headers={"X-CSRF-Token": recipient_registration["csrf_token"]},
        )
        assert revoked.status_code == 200
        assert revoked.json() == {"status": "revoked"}
        assert auth_client.get(f"/api/v1/records/{shared_record.id}").status_code == 403
        after_revoke = auth_client.get(f"/api/v1/families/{family_id}/dashboard")
        assert after_revoke.status_code == 200
        after_revoke_body = after_revoke.json()
        assert after_revoke_body["recent_updates"] == []
        assert after_revoke_body["upcoming_appointments"] == []
        assert after_revoke_body["goal_summary"] == []
        assert after_revoke_body["consents"] == []
