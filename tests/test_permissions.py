from datetime import UTC, datetime
from itertools import product
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.routes.auth.dependencies import AuthenticatedSession
from app.db.models import (
    AuditLog,
    AuthSession,
    EmergencyContact,
    Family,
    FamilyMembership,
    MemberProfile,
    ShareGrant,
    SOSEvent,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import require_health_record_access, require_record_access
from app.security.permissions import Action, Role, Visibility, evaluate_access


@pytest.mark.parametrize(
    ("role", "visibility", "action", "resource", "expected"),
    [
        (
            role,
            visibility,
            action,
            resource,
            (
                role == Role.OWNER
                or role == Role.GUARDIAN
                or (
                    visibility == Visibility.SELECTED
                    and (
                        role in {Role.ADULT, Role.GUARDIAN, Role.DEPENDENT}
                        or (role == Role.VIEWER and action == Action.READ)
                        or (
                            role == Role.EMERGENCY_CONTACT
                            and action == Action.READ
                            and resource == "sos_events"
                        )
                    )
                )
            ),
        )
        for role, visibility, action, resource in product(
            list(Role),
            list(Visibility),
            list(Action),
            ["health_records", "sos_events"],
        )
    ],
)
def test_role_visibility_action_authorization_matrix(
    db_session: Session,
    role: Role,
    visibility: Visibility,
    action: Action,
    resource: str,
    expected: bool,
) -> None:
    actor = User(
        email=f"{uuid4()}@example.com",
        password_hash="unused",
        display_name="Actor",
    )
    target = User(
        email=f"{uuid4()}@example.com",
        password_hash="unused",
        display_name="Target",
    )
    family_owner = User(
        email=f"{uuid4()}@example.com",
        password_hash="unused",
        display_name="Family owner",
    )
    db_session.add_all([actor, target, family_owner])
    db_session.flush()
    target_profile = MemberProfile(
        user_id=(actor.id if role == Role.OWNER else None if role == Role.GUARDIAN else target.id)
    )
    family = Family(name="Test family", created_by_user_id=family_owner.id)
    actor_profile = MemberProfile(user_id=actor.id) if role != Role.OWNER else None
    db_session.add_all([target_profile, family])
    if actor_profile is not None:
        db_session.add(actor_profile)
    db_session.flush()

    actor_role = role.value
    target_role = Role.DEPENDENT.value if role == Role.GUARDIAN else "member"
    if actor_profile is not None:
        db_session.add(
            FamilyMembership(
                family_id=family.id,
                profile_id=actor_profile.id,
                role=actor_role,
                status="active",
            )
        )
    db_session.add(
        FamilyMembership(
            family_id=family.id,
            profile_id=target_profile.id,
            role=target_role,
            status="active",
        )
    )
    record_id = uuid4()
    if visibility == Visibility.SELECTED and role != Role.OWNER:
        db_session.add(
            ShareGrant(
                profile_id=target_profile.id,
                record_id=record_id,
                granted_to_user_id=actor.id,
                access_role=role.value,
                permissions=[action.value],
            )
        )
    db_session.flush()

    decision = evaluate_access(
        db_session,
        actor=actor,
        profile_id=target_profile.id,
        visibility=visibility,
        action=action,
        record_id=record_id,
        resource=resource,
    )

    assert decision.allowed is expected, (role, visibility, action, resource, decision)


def test_denied_health_record_access_is_blocked_and_audited(
    auth_client,
    db_session: Session,
) -> None:
    registration = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]},
        json={
            "email": "access-actor@example.com",
            "password": "StrongPassphrase2026!",
            "display_name": "Actor",
        },
    )
    assert registration.status_code == 201
    actor = db_session.scalar(select(User).where(User.email == "access-actor@example.com"))
    assert actor is not None
    target = User(
        email="access-owner@example.com",
        password_hash="unused",
        display_name="Owner",
    )
    db_session.add(target)
    db_session.flush()
    target_profile = MemberProfile(user_id=target.id)
    db_session.add_all([target, target_profile])
    db_session.flush()
    auth_session = db_session.scalar(select(AuthSession).where(AuthSession.user_id == actor.id))
    assert auth_session is not None
    from app.db.models import HealthRecord, RecordType

    record_type = RecordType(code="access-test", display_name="Access test")
    db_session.add(record_type)
    db_session.flush()
    record = HealthRecord(
        profile_id=target_profile.id,
        type=record_type.code,
        visibility=Visibility.PRIVATE.value,
        value=1,
        unit="unit",
        recorded_at=datetime.now(UTC),
    )
    db_session.add(record)
    db_session.flush()

    dependency = require_health_record_access(Action.READ)
    with pytest.raises(HTTPException) as error:
        dependency(
            record.id,
            AuthenticatedSession(user=actor, session=auth_session),
            db_session,
        )

    assert error.value.status_code == 403
    denied = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "record_access_denied",
            AuditLog.entity_id == record.id,
        )
    )
    assert denied is not None
    assert denied.actor_user_id == actor.id
    assert denied.target_profile_id == target_profile.id


def test_user_access_history_is_scoped_to_their_profile(auth_client, db_session: Session) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]},
        json={
            "email": "history-owner@example.com",
            "password": "StrongPassphrase2026!",
            "display_name": "History owner",
        },
    )
    assert response.status_code == 201
    user = db_session.scalar(select(User).where(User.email == "history-owner@example.com"))
    assert user is not None
    own_profile = db_session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    assert own_profile is not None
    other = User(
        email="other-history-owner@example.com",
        password_hash="unused",
        display_name="Other",
    )
    db_session.add(other)
    db_session.flush()
    other_profile = MemberProfile(user_id=other.id)
    db_session.add(other_profile)
    db_session.flush()

    append_audit_event(
        db_session,
        actor_user_id=user.id,
        target_profile_id=own_profile.id,
        action="record_access_denied",
        entity_type="health_records",
        details={"reason": "private"},
    )
    append_audit_event(
        db_session,
        actor_user_id=user.id,
        target_profile_id=other_profile.id,
        action="record_access_allowed",
        entity_type="health_records",
    )
    db_session.commit()

    history = auth_client.get("/api/v1/audit/access-history")

    assert history.status_code == 200
    assert [event["action"] for event in history.json()] == ["record_access_denied"]
    assert history.headers["cache-control"] == "no-store"


def test_emergency_contact_grants_are_limited_to_emergency_resources(
    auth_client,
    db_session: Session,
) -> None:
    registration = auth_client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]},
        json={
            "email": "emergency-contact@example.com",
            "password": "StrongPassphrase2026!",
            "display_name": "Emergency contact",
        },
    )
    assert registration.status_code == 201
    actor = db_session.scalar(select(User).where(User.email == "emergency-contact@example.com"))
    assert actor is not None
    target = User(
        email="emergency-owner@example.com",
        password_hash="unused",
        display_name="Owner",
    )
    db_session.add(target)
    db_session.flush()
    target_profile = MemberProfile(user_id=target.id)
    db_session.add(target_profile)
    db_session.flush()
    emergency = SOSEvent(
        profile_id=target_profile.id,
        visibility=Visibility.SELECTED.value,
    )
    contact = EmergencyContact(
        profile_id=target_profile.id,
        name="Contact",
        relationship="friend",
        phone_number="+10000000000",
        visibility=Visibility.SELECTED.value,
    )
    db_session.add_all([emergency, contact])
    db_session.flush()
    db_session.add_all(
        [
            ShareGrant(
                profile_id=target_profile.id,
                record_id=emergency.id,
                granted_to_user_id=actor.id,
                access_role=Role.EMERGENCY_CONTACT.value,
                permissions=["read"],
            ),
            ShareGrant(
                profile_id=target_profile.id,
                record_id=contact.id,
                granted_to_user_id=actor.id,
                access_role=Role.EMERGENCY_CONTACT.value,
                permissions=["read"],
            ),
        ]
    )
    db_session.flush()
    auth_session = db_session.scalar(select(AuthSession).where(AuthSession.user_id == actor.id))
    assert auth_session is not None

    authorized = require_record_access(
        SOSEvent,
        "sos_events",
        Action.READ,
    )(emergency.id, AuthenticatedSession(user=actor, session=auth_session), db_session)
    contact_access = require_record_access(
        EmergencyContact,
        "emergency_contacts",
        Action.READ,
    )(contact.id, AuthenticatedSession(user=actor, session=auth_session), db_session)
    health_record_decision = evaluate_access(
        db_session,
        actor=actor,
        profile_id=target_profile.id,
        visibility=Visibility.SELECTED,
        action=Action.READ,
        record_id=emergency.id,
        resource="health_records",
    )

    assert authorized.id == emergency.id
    assert contact_access.id == contact.id
    assert not health_record_decision.allowed


def test_family_owner_does_not_bypass_explicit_consent(db_session: Session) -> None:
    owner = User(email="family-owner@example.com", password_hash="unused", display_name="Owner")
    target = User(email="family-target@example.com", password_hash="unused", display_name="Target")
    db_session.add_all([owner, target])
    db_session.flush()
    target_profile = MemberProfile(user_id=target.id)
    family = Family(name="Owned family", created_by_user_id=owner.id)
    db_session.add_all([target_profile, family])
    db_session.flush()
    db_session.add_all(
        [
            FamilyMembership(family_id=family.id, profile_id=target_profile.id, role="dependent"),
        ]
    )
    db_session.flush()

    decision = evaluate_access(
        db_session,
        actor=owner,
        profile_id=target_profile.id,
        visibility=Visibility.PRIVATE,
        action=Action.DELETE,
    )

    assert not decision.allowed
