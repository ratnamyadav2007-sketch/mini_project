from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.db.models import Family, FamilyMembership, MemberProfile, User
from app.security.audit import append_audit_event
from app.security.dependencies import (
    CurrentAuthenticatedSession,
    DbSession,
)
from app.security.permissions import Action, Role, Visibility, evaluate_access

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    date_of_birth: date | None = None
    sex: str | None = Field(default=None, max_length=32)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    relationship: str = Field(default="dependent", min_length=1, max_length=32)
    avatar_color: str = Field(default="#456c58", pattern=r"^#[0-9a-fA-F]{6}$")
    avatar_wash: str = Field(default="#e5ede5", pattern=r"^#[0-9a-fA-F]{6}$")
    blood_group: str | None = Field(default=None, max_length=3)
    allergies: list[str] = Field(default_factory=list, max_length=50)
    conditions: list[str] = Field(default_factory=list, max_length=50)
    onboarding_step: int = Field(default=0, ge=0, le=2)
    onboarding_complete: bool = False


class DependentProfileCreate(ProfileCreate):
    family_id: UUID


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    date_of_birth: date | None = None
    sex: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    relationship: str | None = Field(default=None, min_length=1, max_length=32)
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    avatar_wash: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    blood_group: str | None = Field(default=None, max_length=3)
    allergies: list[str] | None = Field(default=None, max_length=50)
    conditions: list[str] | None = Field(default=None, max_length=50)
    onboarding_step: int | None = Field(default=None, ge=0, le=2)
    onboarding_complete: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "ProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class ProfileResponse(BaseModel):
    id: UUID
    user_id: UUID | None
    display_name: str
    date_of_birth: date | None
    sex: str | None
    timezone: str
    relationship: str
    avatar_color: str
    avatar_wash: str
    blood_group: str | None
    allergies: list[str]
    conditions: list[str]
    onboarding_step: int
    onboarding_complete: bool


def _profile_response(profile: MemberProfile, owner: User | None) -> ProfileResponse:
    preferences = profile.preferences or {}
    return ProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        display_name=profile.display_name or (owner.display_name if owner else "Dependent"),
        date_of_birth=profile.date_of_birth,
        sex=profile.sex,
        timezone=profile.timezone,
        relationship=preferences.get("relationship", "you" if profile.user_id else "child"),
        avatar_color=preferences.get("avatar_color", "#456c58"),
        avatar_wash=preferences.get("avatar_wash", "#e5ede5"),
        blood_group=preferences.get("blood_group"),
        allergies=preferences.get("allergies", []),
        conditions=preferences.get("conditions", []),
        onboarding_step=preferences.get("onboarding_step", 0),
        onboarding_complete=preferences.get("onboarding_complete", False),
    )


@router.get("", response_model=list[ProfileResponse])
def list_profiles(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[ProfileResponse]:
    own_profiles = list(
        session.scalars(
            select(MemberProfile).where(
                MemberProfile.user_id == authenticated.user.id,
                MemberProfile.deleted_at.is_(None),
            )
        )
    )
    visible_profiles: dict[UUID, MemberProfile] = {profile.id: profile for profile in own_profiles}
    memberships = (
        session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.profile_id.in_([profile.id for profile in own_profiles]),
                FamilyMembership.role.in_([Role.OWNER.value, Role.GUARDIAN.value]),
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
            )
        )
        if own_profiles
        else []
    )
    family_ids = {membership.family_id for membership in memberships}
    if family_ids:
        for membership in session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.family_id.in_(family_ids),
                FamilyMembership.role == Role.DEPENDENT.value,
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
            )
        ):
            profile = session.get(MemberProfile, membership.profile_id)
            if profile is not None and profile.deleted_at is None:
                visible_profiles[profile.id] = profile
    owner_ids = [profile.user_id for profile in visible_profiles.values() if profile.user_id]
    owners = {user.id: user for user in session.scalars(select(User).where(User.id.in_(owner_ids)))}
    response.headers["Cache-Control"] = "no-store"
    return [
        _profile_response(profile, owners.get(profile.user_id))
        for profile in visible_profiles.values()
    ]


@router.post("", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_dependent_profile(
    body: DependentProfileCreate,
    response: Response,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ProfileResponse:
    guardian_profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == authenticated.user.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    if guardian_profile is None:
        raise HTTPException(status_code=403, detail="A guardian profile is required")
    family = session.get(Family, body.family_id)
    guardian_membership = session.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == body.family_id,
            FamilyMembership.profile_id == guardian_profile.id,
            FamilyMembership.role.in_([Role.OWNER.value, Role.GUARDIAN.value]),
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
        )
    )
    if family is None or family.deleted_at is not None or guardian_membership is None:
        raise HTTPException(status_code=403, detail="Only a linked guardian can add a dependent")

    preferences = {
        "relationship": body.relationship,
        "avatar_color": body.avatar_color,
        "avatar_wash": body.avatar_wash,
        "blood_group": body.blood_group,
        "allergies": body.allergies,
        "conditions": body.conditions,
        "onboarding_step": body.onboarding_step,
        "onboarding_complete": body.onboarding_complete,
    }
    profile = MemberProfile(
        user_id=None,
        display_name=body.display_name.strip(),
        date_of_birth=body.date_of_birth,
        sex=body.sex,
        timezone=body.timezone,
        preferences=preferences,
    )
    if not profile.display_name:
        raise HTTPException(status_code=422, detail="Display name must not be blank")
    session.add(profile)
    session.flush()
    session.add(
        FamilyMembership(
            family_id=body.family_id,
            profile_id=profile.id,
            role=Role.DEPENDENT.value,
            status="active",
        )
    )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="dependent_profile_created",
        entity_type="member_profiles",
        entity_id=profile.id,
        details={"family_id": str(body.family_id)},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _profile_response(profile, None)


@router.patch("/{profile_id}", response_model=ProfileResponse)
def update_profile(
    profile_id: UUID,
    body: ProfileUpdate,
    response: Response,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ProfileResponse:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    decision = evaluate_access(
        session,
        actor=authenticated.user,
        profile_id=profile.id,
        visibility=Visibility.PRIVATE,
        action=Action.WRITE,
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile.id,
            action="profile_update_denied",
            entity_type="member_profiles",
            entity_id=profile.id,
            details={"reason": decision.reason},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")

    values = body.model_dump(exclude_unset=True)
    preferences = dict(profile.preferences or {})
    preference_fields = {
        "relationship",
        "avatar_color",
        "avatar_wash",
        "blood_group",
        "allergies",
        "conditions",
        "onboarding_step",
        "onboarding_complete",
    }
    for key, value in values.items():
        if key in preference_fields:
            preferences[key] = value
        elif key == "display_name":
            if value is None:
                raise HTTPException(status_code=422, detail="Display name must not be blank")
            cleaned = value.strip()
            if not cleaned:
                raise HTTPException(status_code=422, detail="Display name must not be blank")
            profile.display_name = cleaned
        else:
            setattr(profile, key, value)
    profile.preferences = preferences
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="profile_updated",
        entity_type="member_profiles",
        entity_id=profile.id,
    )
    session.commit()
    session.refresh(profile)
    owner = session.get(User, profile.user_id) if profile.user_id else None
    response.headers["Cache-Control"] = "no-store"
    return _profile_response(profile, owner)


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> ProfileResponse:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    decision = evaluate_access(
        session,
        actor=authenticated.user,
        profile_id=profile.id,
        visibility=Visibility.PRIVATE,
        action=Action.READ,
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile.id,
            action="profile_access_denied",
            entity_type="member_profiles",
            entity_id=profile.id,
            details={"reason": decision.reason},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")
    owner = session.get(User, profile.user_id) if profile.user_id else None
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="profile_access_allowed",
        entity_type="member_profiles",
        entity_id=profile.id,
        details={"role": decision.role.value if decision.role else "unknown"},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _profile_response(profile, owner)
