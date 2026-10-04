from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import (
    CurrentCsrfAuthenticatedSession,
    clear_session_cookie,
)
from app.db.models import AuthSession, FamilyMembership, MemberProfile, ShareGrant
from app.security.audit import append_audit_event
from app.security.dependencies import DbSession
from app.services.account_deletion import FamilyCustodyError, transfer_family_ownership

router = APIRouter(prefix="/auth", tags=["auth"])
DELETION_GRACE_DAYS = 30


@router.delete("/account", status_code=202)
def request_account_deletion(
    response: Response,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> dict[str, str]:
    user = authenticated.user
    now = datetime.now(UTC)
    user.deleted_at = now
    user.purge_after = now + timedelta(days=DELETION_GRACE_DAYS)
    user.is_active = False
    profiles = list(
        session.scalars(
            select(MemberProfile).where(
                MemberProfile.user_id == user.id,
                MemberProfile.deleted_at.is_(None),
            )
        )
    )
    profile_ids = [profile.id for profile in profiles]
    for profile in profiles:
        profile.deleted_at = now
    if profile_ids:
        memberships = session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.profile_id.in_(profile_ids),
                FamilyMembership.deleted_at.is_(None),
            )
        )
        for membership in memberships:
            membership.status = "removed"
            membership.deleted_at = now
    for grant in session.scalars(
        select(ShareGrant).where(
            ShareGrant.granted_to_user_id == user.id,
            ShareGrant.revoked_at.is_(None),
            ShareGrant.deleted_at.is_(None),
        )
    ):
        grant.revoked_at = now
    try:
        transfer_family_ownership(session, user.id, now)
    except FamilyCustodyError as exception:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Assign another active Guardian to each dependent profile before deleting "
                "this account"
            ),
        ) from exception
    for auth_session in session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        auth_session.revoked_at = now
    append_audit_event(
        session,
        actor_user_id=user.id,
        target_profile_id=profile_ids[0] if profile_ids else None,
        action="account_deletion_requested",
        entity_type="users",
        entity_id=user.id,
        details={"purge_after": user.purge_after.isoformat()},
    )
    session.commit()
    clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "deletion_scheduled",
        "purge_after": user.purge_after.isoformat(),
        "grace_period_days": str(DELETION_GRACE_DAYS),
    }
