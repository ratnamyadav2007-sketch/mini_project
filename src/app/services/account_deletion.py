from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Family, FamilyMembership, MemberProfile, User


class FamilyCustodyError(ValueError):
    pass


def transfer_family_ownership(session: Session, user_id: UUID, now: datetime) -> None:
    actor_profile_ids = select(MemberProfile.id).where(MemberProfile.user_id == user_id)
    affected_family_ids = set(
        session.scalars(
            select(FamilyMembership.family_id).where(
                FamilyMembership.profile_id.in_(actor_profile_ids)
            )
        )
    )
    affected_family_ids.update(
        session.scalars(
            select(Family.id).where(
                Family.created_by_user_id == user_id,
                Family.deleted_at.is_(None),
            )
        )
    )
    for family in session.scalars(
        select(Family).where(
            Family.id.in_(affected_family_ids),
            Family.deleted_at.is_(None),
        )
    ):
        memberships = list(
            session.scalars(
                select(FamilyMembership).where(
                    FamilyMembership.family_id == family.id,
                    FamilyMembership.status == "active",
                    FamilyMembership.deleted_at.is_(None),
                )
            )
        )
        dependents: list[FamilyMembership] = []
        successor: tuple[FamilyMembership, MemberProfile] | None = None
        for membership in memberships:
            profile = session.get(MemberProfile, membership.profile_id)
            if (
                membership.role == "dependent"
                and profile is not None
                and profile.user_id is None
                and profile.deleted_at is None
            ):
                dependents.append(membership)
            if (
                profile is not None
                and profile.user_id is not None
                and profile.user_id != user_id
                and profile.deleted_at is None
                and membership.role in {"owner", "adult", "guardian"}
            ):
                candidate_user = session.get(User, profile.user_id)
                if (
                    successor is None
                    and candidate_user is not None
                    and candidate_user.is_active
                    and candidate_user.deleted_at is None
                ):
                    successor = membership, profile
        if successor is not None:
            membership, profile = successor
            assert profile.user_id is not None
            is_creator = family.created_by_user_id == user_id
            if is_creator:
                family.created_by_user_id = profile.user_id
            if dependents:
                membership.role = "guardian"
            elif is_creator:
                membership.role = "owner"
        elif dependents:
            raise FamilyCustodyError(
                f"Family {family.id} has dependent profiles with no other active Guardian"
            )
        elif family.created_by_user_id == user_id:
            family.deleted_at = now
