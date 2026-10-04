import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.db.models import (
    Appointment,
    Family,
    FamilyInvite,
    FamilyMembership,
    Goal,
    HealthRecord,
    MemberProfile,
    ShareGrant,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import CurrentAuthenticatedSession, DbSession
from app.security.permissions import Action, Role, evaluate_access

router = APIRouter(tags=["families and consent"])
INVITABLE_ROLES = frozenset({Role.ADULT, Role.GUARDIAN, Role.DEPENDENT, Role.VIEWER})
CONSENT_RESOURCES = frozenset({"health_records", "appointments", "goals"})


class FamilyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def non_blank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Family name must not be blank")
        return value


class FamilyResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class MemberResponse(BaseModel):
    profile_id: UUID
    user_id: UUID | None
    display_name: str
    email: str | None
    role: Role
    joined_at: datetime


class InviteCreate(BaseModel):
    role: Role
    expires_in_hours: int = Field(default=72, ge=1, le=720)

    @field_validator("role")
    @classmethod
    def require_invitable_role(cls, value: Role) -> Role:
        if value not in INVITABLE_ROLES:
            raise ValueError("This role cannot be assigned by invitation")
        return value


class InviteResponse(BaseModel):
    id: UUID
    family_id: UUID
    role: Role
    invite_code: str
    expires_at: datetime


class InviteDecision(BaseModel):
    invite_code: str = Field(min_length=20, max_length=128)


class ConsentCreate(BaseModel):
    profile_id: UUID
    recipient_profile_id: UUID
    resources: list[str] = Field(min_length=1, max_length=3)
    expires_at: datetime | None = None

    @field_validator("resources")
    @classmethod
    def validate_resources(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().casefold() for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Consent resources must be unique")
        if any(value not in CONSENT_RESOURCES for value in normalized):
            raise ValueError("Unsupported consent resource")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        value = value.astimezone(UTC)
        if value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


class ConsentResponse(BaseModel):
    id: UUID
    family_id: UUID
    profile_id: UUID
    profile_name: str
    recipient_user_id: UUID
    recipient_profile_id: UUID
    recipient_name: str
    record_id: UUID | None
    resources: list[str]
    permissions: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None


class ConsentRevokeResponse(BaseModel):
    status: str


class ConsentUpdate(BaseModel):
    resources: list[str] = Field(max_length=3)

    @field_validator("resources")
    @classmethod
    def validate_resources(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().casefold() for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Consent resources must be unique")
        if any(value not in CONSENT_RESOURCES for value in normalized):
            raise ValueError("Unsupported consent resource")
        return normalized


class MemberRoleUpdate(BaseModel):
    role: Role

    @field_validator("role")
    @classmethod
    def require_assignable_role(cls, value: Role) -> Role:
        if value not in INVITABLE_ROLES:
            raise ValueError("This role cannot be assigned to a family member")
        return value


class DashboardUpdate(BaseModel):
    id: UUID
    profile_id: UUID
    type: str
    recorded_at: datetime
    visibility: str


class DashboardAppointment(BaseModel):
    id: UUID
    profile_id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None
    location: str | None


class GoalSummary(BaseModel):
    profile_id: UUID
    active: int
    completed: int
    other: int


class FamilyDashboard(BaseModel):
    family_id: UUID
    family_name: str
    current_profile_id: UUID
    current_role: Role
    members: list[MemberResponse]
    consents: list[ConsentResponse]
    recent_updates: list[DashboardUpdate]
    upcoming_appointments: list[DashboardAppointment]
    goal_summary: list[GoalSummary]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _user_profile(session: DbSession, user: User) -> MemberProfile:
    profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == user.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    if profile is None:
        raise HTTPException(status_code=409, detail="The account has no active member profile")
    return profile


def _family_membership(
    session: DbSession,
    family_id: UUID,
    profile_id: UUID,
) -> FamilyMembership:
    family = session.get(Family, family_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family not found")
    membership = session.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == family_id,
            FamilyMembership.profile_id == profile_id,
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Active family membership is required")
    return membership


def _actor_family_membership(
    session: DbSession,
    family_id: UUID,
    actor: User,
) -> tuple[MemberProfile, FamilyMembership]:
    profile = _user_profile(session, actor)
    return profile, _family_membership(session, family_id, profile.id)


def _require_family_manager(membership: FamilyMembership) -> None:
    if membership.role.casefold() not in {Role.OWNER.value, Role.GUARDIAN.value}:
        raise HTTPException(status_code=403, detail="Owner or Guardian role is required")


def _active_membership(
    session: DbSession,
    family_id: UUID,
    profile_id: UUID,
) -> FamilyMembership | None:
    return session.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == family_id,
            FamilyMembership.profile_id == profile_id,
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
        )
    )


def _profile_name(session: DbSession, profile: MemberProfile) -> str:
    if profile.display_name:
        return profile.display_name
    if profile.user_id is not None:
        owner = session.get(User, profile.user_id)
        if owner is not None:
            return owner.display_name
    return "Dependent"


def _profile_email(session: DbSession, profile: MemberProfile) -> str | None:
    if profile.user_id is None:
        return None
    user = session.get(User, profile.user_id)
    return user.email if user is not None else None


def _member_response(
    session: DbSession, membership: FamilyMembership, profile: MemberProfile
) -> MemberResponse:
    try:
        role = Role(membership.role.casefold())
    except ValueError as exception:
        raise RuntimeError("Family membership has an unsupported role") from exception
    return MemberResponse(
        profile_id=profile.id,
        user_id=profile.user_id,
        display_name=_profile_name(session, profile),
        email=_profile_email(session, profile),
        role=role,
        joined_at=membership.created_at,
    )


@router.post("/families", response_model=FamilyResponse, status_code=status.HTTP_201_CREATED)
def create_family(
    body: FamilyCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> FamilyResponse:
    profile = _user_profile(session, authenticated.user)
    family = Family(name=body.name, created_by_user_id=authenticated.user.id)
    session.add(family)
    session.flush()
    session.add(
        FamilyMembership(
            family_id=family.id,
            profile_id=profile.id,
            role=Role.OWNER.value,
            status="active",
        )
    )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_created",
        entity_type="families",
        entity_id=family.id,
    )
    session.commit()
    session.refresh(family)
    return FamilyResponse(id=family.id, name=family.name, created_at=family.created_at)


@router.get("/families", response_model=list[FamilyResponse])
def list_families(
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[FamilyResponse]:
    profile = _user_profile(session, authenticated.user)
    families = session.scalars(
        select(Family)
        .join(FamilyMembership, FamilyMembership.family_id == Family.id)
        .where(
            FamilyMembership.profile_id == profile.id,
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
            Family.deleted_at.is_(None),
        )
        .order_by(Family.created_at, Family.id)
    )
    return [
        FamilyResponse(id=family.id, name=family.name, created_at=family.created_at)
        for family in families
    ]


@router.get("/families/{family_id}/members", response_model=list[MemberResponse])
def list_family_members(
    family_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[MemberResponse]:
    _actor_family_membership(session, family_id, authenticated.user)
    memberships = session.scalars(
        select(FamilyMembership)
        .where(
            FamilyMembership.family_id == family_id,
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
        )
        .order_by(FamilyMembership.created_at, FamilyMembership.id)
    )
    response.headers["Cache-Control"] = "no-store"
    output: list[MemberResponse] = []
    for membership in memberships:
        profile = session.get(MemberProfile, membership.profile_id)
        if profile is None or profile.deleted_at is not None:
            continue
        output.append(_member_response(session, membership, profile))
    return output


@router.post(
    "/families/{family_id}/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_family_invite(
    family_id: UUID,
    body: InviteCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> InviteResponse:
    _, membership = _actor_family_membership(session, family_id, authenticated.user)
    _require_family_manager(membership)
    family = session.get(Family, family_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family not found")
    raw_code = secrets.token_urlsafe(32)
    invite = FamilyInvite(
        family_id=family_id,
        invite_code_hash=hashlib.sha256(raw_code.encode("ascii")).hexdigest(),
        role=body.role.value,
        created_by_user_id=authenticated.user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=body.expires_in_hours),
    )
    session.add(invite)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=None,
        action="family_invite_created",
        entity_type="family_invites",
        entity_id=invite.id,
        details={"family_id": str(family_id), "role": body.role.value},
    )
    session.commit()
    session.refresh(invite)
    return InviteResponse(
        id=invite.id,
        family_id=family_id,
        role=body.role,
        invite_code=raw_code,
        expires_at=invite.expires_at,
    )


def _find_invite(session: DbSession, raw_code: str) -> FamilyInvite:
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    invite = session.scalar(
        select(FamilyInvite)
        .where(
            FamilyInvite.invite_code_hash == code_hash,
            FamilyInvite.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite code is invalid")
    if invite.accepted_at is not None or invite.declined_at is not None:
        raise HTTPException(status_code=409, detail="Invite has already been used")
    if _utc(invite.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Invite has expired")
    return invite


@router.post("/family-invites/accept", response_model=MemberResponse)
def accept_family_invite(
    body: InviteDecision,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> MemberResponse:
    invite = _find_invite(session, body.invite_code)
    family = session.get(Family, invite.family_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=410, detail="Invited family is no longer active")
    profile = _user_profile(session, authenticated.user)
    if invite.created_by_user_id == authenticated.user.id:
        raise HTTPException(status_code=409, detail="You cannot accept your own invite")
    existing = session.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == invite.family_id,
            FamilyMembership.profile_id == profile.id,
        )
    )
    if existing is not None and existing.status == "active" and existing.deleted_at is None:
        raise HTTPException(status_code=409, detail="Profile is already a family member")
    if existing is None:
        membership = FamilyMembership(
            family_id=invite.family_id,
            profile_id=profile.id,
            role=invite.role,
            status="active",
        )
        session.add(membership)
    else:
        membership = existing
        membership.role = invite.role
        membership.status = "active"
        membership.deleted_at = None
    invite.accepted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_invite_accepted",
        entity_type="families",
        entity_id=family.id,
        details={"invite_id": str(invite.id), "role": invite.role},
    )
    session.commit()
    session.refresh(membership)
    return _member_response(session, membership, profile)


@router.post("/family-invites/decline", status_code=status.HTTP_204_NO_CONTENT)
def decline_family_invite(
    body: InviteDecision,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    invite = _find_invite(session, body.invite_code)
    if invite.created_by_user_id == authenticated.user.id:
        raise HTTPException(status_code=409, detail="You cannot decline your own invite")
    invite.declined_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        action="family_invite_declined",
        entity_type="family_invites",
        entity_id=invite.id,
        details={"family_id": str(invite.family_id)},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/families/{family_id}/members/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_family_member(
    family_id: UUID,
    profile_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    actor_profile, actor_membership = _actor_family_membership(
        session,
        family_id,
        authenticated.user,
    )
    target = _active_membership(session, family_id, profile_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Family member not found")
    target_profile = session.get(MemberProfile, profile_id)
    if target_profile is None or target_profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family member not found")
    is_self = profile_id == actor_profile.id
    if not is_self:
        _require_family_manager(actor_membership)
    family_closed = False
    if target.role.casefold() == Role.OWNER.value:
        if not is_self:
            raise HTTPException(status_code=409, detail="Only the family owner can leave")
        family = session.get(Family, family_id)
        if family is None or family.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Family not found")
        candidates = list(
            session.scalars(
                select(FamilyMembership).where(
                    FamilyMembership.family_id == family_id,
                    FamilyMembership.profile_id != profile_id,
                    FamilyMembership.status == "active",
                    FamilyMembership.deleted_at.is_(None),
                )
            )
        )
        successor: tuple[FamilyMembership, MemberProfile] | None = None
        for candidate_role in (Role.GUARDIAN.value, Role.ADULT.value):
            for candidate in candidates:
                candidate_profile = session.get(MemberProfile, candidate.profile_id)
                if (
                    candidate.role.casefold() == candidate_role
                    and candidate_profile is not None
                    and candidate_profile.user_id is not None
                    and candidate_profile.deleted_at is None
                ):
                    successor = candidate, candidate_profile
                    break
            if successor is not None:
                break
        if successor is None:
            family.deleted_at = datetime.now(UTC)
            family_closed = True
        else:
            successor_membership, successor_profile = successor
            successor_membership.role = Role.OWNER.value
            family.created_by_user_id = successor_profile.user_id
    target.status = "removed"
    target.deleted_at = datetime.now(UTC)
    if target_profile.user_id is not None:
        revoke = session.scalars(
            select(ShareGrant).where(
                ShareGrant.granted_to_user_id == target_profile.user_id,
                ShareGrant.profile_id.in_(
                    select(FamilyMembership.profile_id).where(
                        FamilyMembership.family_id == family_id,
                    )
                ),
                ShareGrant.revoked_at.is_(None),
                ShareGrant.deleted_at.is_(None),
            )
        )
        now = datetime.now(UTC)
        for grant in revoke:
            grant.revoked_at = now
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="family_member_removed",
        entity_type="families",
        entity_id=family_id,
        details={
            "profile_id": str(profile_id),
            "self_removed": str(is_self).lower(),
            "family_closed": str(family_closed).lower(),
        },
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/families/{family_id}/members/{profile_id}",
    response_model=MemberResponse,
)
def update_family_member_role(
    family_id: UUID,
    profile_id: UUID,
    body: MemberRoleUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> MemberResponse:
    _, actor_membership = _actor_family_membership(session, family_id, authenticated.user)
    _require_family_manager(actor_membership)
    membership = _active_membership(session, family_id, profile_id)
    profile = session.get(MemberProfile, profile_id)
    if membership is None or profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family member not found")
    if membership.role.casefold() == Role.OWNER.value:
        raise HTTPException(status_code=409, detail="Family owner role cannot be changed")
    previous_role = membership.role
    membership.role = body.role.value
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_member_role_changed",
        entity_type="families",
        entity_id=family_id,
        details={"from": previous_role, "to": body.role.value},
    )
    session.commit()
    session.refresh(membership)
    return _member_response(session, membership, profile)


def _can_manage_profile_consent(
    session: DbSession,
    actor: User,
    profile: MemberProfile,
    family_id: UUID,
) -> bool:
    if profile.user_id == actor.id:
        return True
    if profile.user_id is not None:
        return False
    actor_profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == actor.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    if actor_profile is None:
        return False
    actor_membership = _active_membership(session, family_id, actor_profile.id)
    target_membership = _active_membership(session, family_id, profile.id)
    return (
        actor_membership is not None
        and actor_membership.role.casefold() in {Role.OWNER.value, Role.GUARDIAN.value}
        and target_membership is not None
        and target_membership.role.casefold() == Role.DEPENDENT.value
    )


def _consent_response(
    session: DbSession,
    family_id: UUID,
    grant: ShareGrant,
) -> ConsentResponse | None:
    profile = session.get(MemberProfile, grant.profile_id)
    recipient = session.get(User, grant.granted_to_user_id)
    if profile is None or recipient is None:
        return None
    recipient_profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == recipient.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    if recipient_profile is None:
        return None
    return ConsentResponse(
        id=grant.id,
        family_id=family_id,
        profile_id=profile.id,
        profile_name=_profile_name(session, profile),
        recipient_user_id=recipient.id,
        recipient_profile_id=recipient_profile.id,
        recipient_name=_profile_name(session, recipient_profile),
        record_id=grant.record_id,
        resources=grant.resources,
        permissions=grant.permissions,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
    )


@router.post(
    "/families/{family_id}/consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_consent(
    family_id: UUID,
    body: ConsentCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ConsentResponse:
    _actor_family_membership(session, family_id, authenticated.user)
    family = session.get(Family, family_id)
    profile = session.get(MemberProfile, body.profile_id)
    recipient_profile = session.get(MemberProfile, body.recipient_profile_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family not found")
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Sharing profile not found")
    if recipient_profile is None or recipient_profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Recipient profile not found")
    recipient_membership = _active_membership(session, family_id, recipient_profile.id)
    if recipient_membership is None or recipient_profile.user_id is None:
        raise HTTPException(status_code=422, detail="Recipient must be an active family user")
    if not _can_manage_profile_consent(
        session,
        authenticated.user,
        profile,
        family_id,
    ):
        raise HTTPException(
            status_code=403, detail="Only the profile owner or linked Guardian can consent"
        )
    if profile.id == recipient_profile.id:
        raise HTTPException(status_code=422, detail="A profile cannot be shared with itself")

    grant = ShareGrant(
        profile_id=profile.id,
        record_id=None,
        granted_to_user_id=recipient_profile.user_id,
        access_role=recipient_membership.role,
        permissions=[Action.READ.value],
        resources=body.resources,
        expires_at=body.expires_at,
    )
    session.add(grant)
    session.flush()
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_consent_granted",
        entity_type="share_grants",
        entity_id=grant.id,
        details={
            "family_id": str(family_id),
            "recipient_profile_id": str(recipient_profile.id),
            "resources": ",".join(body.resources),
        },
    )
    session.commit()
    session.refresh(grant)
    response = _consent_response(session, family_id, grant)
    if response is None:
        raise RuntimeError("Created family consent could not be represented")
    return response


@router.get("/families/{family_id}/consents", response_model=list[ConsentResponse])
def list_family_consents(
    family_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[ConsentResponse]:
    _actor_family_membership(session, family_id, authenticated.user)
    family_profile_ids = select(FamilyMembership.profile_id).where(
        FamilyMembership.family_id == family_id,
        FamilyMembership.status == "active",
        FamilyMembership.deleted_at.is_(None),
    )
    grants = list(
        session.scalars(
            select(ShareGrant)
            .where(
                ShareGrant.profile_id.in_(family_profile_ids),
                ShareGrant.record_id.is_(None),
                ShareGrant.deleted_at.is_(None),
            )
            .order_by(ShareGrant.created_at.desc(), ShareGrant.id.desc())
        )
    )
    response.headers["Cache-Control"] = "no-store"
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        action="family_consents_read",
        entity_type="families",
        entity_id=family_id,
    )
    session.commit()
    result: list[ConsentResponse] = []
    for grant in grants:
        consent = _consent_response(session, family_id, grant)
        if consent is not None:
            result.append(consent)
    return result


@router.patch(
    "/families/{family_id}/consents/{grant_id}",
    response_model=ConsentResponse,
)
def update_family_consent(
    family_id: UUID,
    grant_id: UUID,
    body: ConsentUpdate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ConsentResponse:
    _actor_family_membership(session, family_id, authenticated.user)
    grant = session.get(ShareGrant, grant_id)
    if (
        grant is None
        or grant.record_id is not None
        or grant.revoked_at is not None
        or grant.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Active consent was not found")
    profile = session.get(MemberProfile, grant.profile_id)
    if profile is None or _active_membership(session, family_id, profile.id) is None:
        raise HTTPException(status_code=404, detail="Consent was not found in this family")
    if not _can_manage_profile_consent(session, authenticated.user, profile, family_id):
        raise HTTPException(
            status_code=403, detail="Only the sharing profile owner can update consent"
        )
    previous_resources = list(grant.resources)
    grant.resources = body.resources
    if not body.resources:
        grant.revoked_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_consent_updated" if body.resources else "family_consent_revoked",
        entity_type="share_grants",
        entity_id=grant.id,
        details={
            "family_id": str(family_id),
            "from": ",".join(previous_resources),
            "to": ",".join(body.resources),
        },
    )
    session.commit()
    session.refresh(grant)
    response = _consent_response(session, family_id, grant)
    if response is None:
        raise RuntimeError("Updated family consent could not be represented")
    return response


@router.delete(
    "/families/{family_id}/consents/{grant_id}",
    response_model=ConsentRevokeResponse,
)
def revoke_family_consent(
    family_id: UUID,
    grant_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ConsentRevokeResponse:
    _actor_family_membership(session, family_id, authenticated.user)
    grant = session.get(ShareGrant, grant_id)
    if (
        grant is None
        or grant.record_id is not None
        or grant.revoked_at is not None
        or grant.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Active consent was not found")
    profile = session.get(MemberProfile, grant.profile_id)
    if profile is None or _active_membership(session, family_id, profile.id) is None:
        raise HTTPException(status_code=404, detail="Consent was not found in this family")
    if not _can_manage_profile_consent(session, authenticated.user, profile, family_id):
        raise HTTPException(
            status_code=403, detail="Only the sharing profile owner can revoke consent"
        )
    grant.revoked_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="family_consent_revoked",
        entity_type="share_grants",
        entity_id=grant.id,
        details={"family_id": str(family_id)},
    )
    session.commit()
    return ConsentRevokeResponse(status="revoked")


@router.get("/families/{family_id}/dashboard", response_model=FamilyDashboard)
def family_dashboard(
    family_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> FamilyDashboard:
    actor_profile, actor_membership = _actor_family_membership(
        session,
        family_id,
        authenticated.user,
    )
    family = session.get(Family, family_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family not found")
    memberships = list(
        session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
            )
        )
    )
    profile_ids = {membership.profile_id for membership in memberships}
    profile_ids.add(actor_profile.id)
    members: list[MemberResponse] = []
    for membership in memberships:
        profile = session.get(MemberProfile, membership.profile_id)
        if profile is not None and profile.deleted_at is None:
            members.append(_member_response(session, membership, profile))

    now = datetime.now(UTC)
    consents: list[ConsentResponse] = []
    grants = session.scalars(
        select(ShareGrant).where(
            ShareGrant.profile_id.in_(profile_ids),
            ShareGrant.record_id.is_(None),
            ShareGrant.revoked_at.is_(None),
            ShareGrant.deleted_at.is_(None),
        )
    )
    for grant in grants:
        if grant.expires_at is not None and _utc(grant.expires_at) <= now:
            continue
        consent = _consent_response(session, family_id, grant)
        if (
            consent is not None
            and consent.recipient_profile_id in profile_ids
            and consent.profile_id in profile_ids
        ):
            consents.append(consent)

    recent_updates: list[DashboardUpdate] = []
    records = session.scalars(
        select(HealthRecord)
        .where(
            HealthRecord.profile_id.in_(profile_ids),
            HealthRecord.deleted_at.is_(None),
        )
        .order_by(HealthRecord.recorded_at.desc(), HealthRecord.id.desc())
    )
    for record in records:
        decision = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=record.profile_id,
            visibility=record.visibility,
            action=Action.READ,
            record_id=record.id,
            resource="health_records",
        )
        if decision.allowed:
            recent_updates.append(
                DashboardUpdate(
                    id=record.id,
                    profile_id=record.profile_id,
                    type=record.type,
                    recorded_at=record.recorded_at,
                    visibility=record.visibility,
                )
            )
            if len(recent_updates) == 10:
                break

    upcoming_appointments: list[DashboardAppointment] = []
    appointments = session.scalars(
        select(Appointment)
        .where(
            Appointment.profile_id.in_(profile_ids),
            Appointment.deleted_at.is_(None),
            Appointment.starts_at >= now,
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.starts_at, Appointment.id)
    )
    for appointment in appointments:
        decision = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=appointment.profile_id,
            visibility=appointment.visibility,
            action=Action.READ,
            resource="appointments",
        )
        if decision.allowed:
            upcoming_appointments.append(
                DashboardAppointment(
                    id=appointment.id,
                    profile_id=appointment.profile_id,
                    title=appointment.title,
                    starts_at=appointment.starts_at,
                    ends_at=appointment.ends_at,
                    location=appointment.location,
                )
            )
            if len(upcoming_appointments) == 10:
                break

    goal_counts: dict[UUID, dict[str, int]] = {}
    goals = session.scalars(
        select(Goal).where(
            Goal.profile_id.in_(profile_ids),
            Goal.deleted_at.is_(None),
        )
    )
    for goal in goals:
        decision = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=goal.profile_id,
            visibility=goal.visibility,
            action=Action.READ,
            resource="goals",
        )
        if not decision.allowed:
            continue
        counts = goal_counts.setdefault(goal.profile_id, {"active": 0, "completed": 0, "other": 0})
        if goal.status.casefold() == "active":
            counts["active"] += 1
        elif goal.status.casefold() == "completed":
            counts["completed"] += 1
        else:
            counts["other"] += 1

    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        action="family_dashboard_read",
        entity_type="families",
        entity_id=family_id,
        details={
            "updates": str(len(recent_updates)),
            "appointments": str(len(upcoming_appointments)),
            "profiles_with_goals": str(len(goal_counts)),
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return FamilyDashboard(
        family_id=family.id,
        family_name=family.name,
        current_profile_id=actor_profile.id,
        current_role=Role(actor_membership.role.casefold()),
        members=members,
        consents=consents,
        recent_updates=recent_updates,
        upcoming_appointments=upcoming_appointments,
        goal_summary=[
            GoalSummary(profile_id=profile_id, **counts)
            for profile_id, counts in goal_counts.items()
        ],
    )
