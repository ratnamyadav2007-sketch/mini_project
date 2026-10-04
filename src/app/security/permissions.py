from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Family, FamilyMembership, MemberProfile, ShareGrant, User


class Role(StrEnum):
    OWNER = "owner"
    ADULT = "adult"
    GUARDIAN = "guardian"
    DEPENDENT = "dependent"
    VIEWER = "viewer"
    EMERGENCY_CONTACT = "emergency_contact"


class Visibility(StrEnum):
    PRIVATE = "private"
    SELECTED = "selected"
    FAMILY = "family"


class Action(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


EMERGENCY_RESOURCES = frozenset({"sos_events", "emergency_contacts"})


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    role: Role | None
    reason: str


def _active_memberships(session: Session, profile_id: UUID) -> list[FamilyMembership]:
    return list(
        session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.profile_id == profile_id,
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
                FamilyMembership.family_id == Family.id,
                Family.deleted_at.is_(None),
            )
        )
    )


def _matching_share_grant(
    session: Session,
    *,
    user_id: UUID,
    profile_id: UUID,
    record_id: UUID | None,
    action: Action,
) -> Role | None:
    now = datetime.now(UTC)
    grants = session.scalars(
        select(ShareGrant).where(
            ShareGrant.profile_id == profile_id,
            ShareGrant.granted_to_user_id == user_id,
            ShareGrant.record_id == record_id,
            ShareGrant.revoked_at.is_(None),
            ShareGrant.deleted_at.is_(None),
        )
    )
    for grant in grants:
        if (
            grant.expires_at is None or _utc(grant.expires_at) > now
        ) and action.value in grant.permissions:
            try:
                return Role(grant.access_role.casefold())
            except ValueError:
                continue
    return None


def _matching_profile_grant(
    session: Session,
    *,
    user_id: UUID,
    profile_id: UUID,
    resource: str,
    action: Action,
) -> Role | None:
    now = datetime.now(UTC)
    grants = session.scalars(
        select(ShareGrant).where(
            ShareGrant.profile_id == profile_id,
            ShareGrant.granted_to_user_id == user_id,
            ShareGrant.record_id.is_(None),
            ShareGrant.revoked_at.is_(None),
            ShareGrant.deleted_at.is_(None),
        )
    )
    for grant in grants:
        if (
            resource in grant.resources
            and action.value in grant.permissions
            and (grant.expires_at is None or _utc(grant.expires_at) > now)
        ):
            try:
                return Role(grant.access_role.casefold())
            except ValueError:
                continue
    return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluate_access(
    session: Session,
    *,
    actor: User,
    profile_id: UUID,
    visibility: Visibility | str,
    action: Action | str,
    record_id: UUID | None = None,
    resource: str = "health_records",
) -> AccessDecision:
    try:
        record_visibility = Visibility(visibility)
        requested_action = Action(action)
    except ValueError:
        return AccessDecision(False, None, "invalid_visibility_or_action")

    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        return AccessDecision(False, None, "profile_not_found")
    if not actor.is_active or actor.deleted_at is not None:
        return AccessDecision(False, None, "inactive_actor")
    if profile.user_id == actor.id:
        return AccessDecision(True, Role.OWNER, "profile_owner")

    target_memberships = _active_memberships(session, profile_id)

    actor_profiles = session.scalars(
        select(MemberProfile).where(
            MemberProfile.user_id == actor.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    actor_memberships = [
        membership
        for actor_profile in actor_profiles
        for membership in _active_memberships(session, actor_profile.id)
    ]
    target_by_family = {membership.family_id: membership for membership in target_memberships}
    actor_roles: list[Role] = []
    for membership in actor_memberships:
        if membership.family_id not in target_by_family:
            continue
        try:
            actor_role = Role(membership.role.casefold())
        except ValueError:
            continue
        actor_roles.append(actor_role)
        target_role = target_by_family[membership.family_id].role.casefold()

        if (
            actor_role in {Role.OWNER, Role.GUARDIAN}
            and target_role == Role.DEPENDENT
            and profile.user_id is None
        ):
            return AccessDecision(True, actor_role, "family_manager_dependent_access")

    if record_visibility == Visibility.SELECTED and record_id is not None:
        granted_role = _matching_share_grant(
            session,
            user_id=actor.id,
            profile_id=profile_id,
            record_id=record_id,
            action=requested_action,
        )
        if granted_role in {
            Role.ADULT,
            Role.GUARDIAN,
            Role.DEPENDENT,
            Role.VIEWER,
            Role.EMERGENCY_CONTACT,
        }:
            if granted_role == Role.VIEWER and requested_action != Action.READ:
                return AccessDecision(False, granted_role, "viewer_is_read_only")
            if granted_role == Role.EMERGENCY_CONTACT and (
                requested_action != Action.READ or resource not in EMERGENCY_RESOURCES
            ):
                return AccessDecision(False, granted_role, "emergency_contact_scope")
            return AccessDecision(True, granted_role, "selected_share_grant")

    if record_visibility != Visibility.PRIVATE:
        granted_role = _matching_profile_grant(
            session,
            user_id=actor.id,
            profile_id=profile_id,
            resource=resource,
            action=requested_action,
        )
        if granted_role in {
            Role.OWNER,
            Role.ADULT,
            Role.GUARDIAN,
            Role.DEPENDENT,
            Role.VIEWER,
            Role.EMERGENCY_CONTACT,
        }:
            if (
                granted_role in {Role.VIEWER, Role.EMERGENCY_CONTACT}
                and requested_action != Action.READ
            ):
                return AccessDecision(False, granted_role, "consent_is_read_only")
            return AccessDecision(True, granted_role, "profile_consent_grant")

    return AccessDecision(
        False,
        actor_roles[0] if actor_roles else None,
        "relationship_or_visibility_denied",
    )
