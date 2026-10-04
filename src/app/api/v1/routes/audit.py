from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import MemberProfile
from app.security.audit import list_profile_access_history
from app.security.dependencies import CurrentAuthenticatedSession, DbSession

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    details: dict[str, Any]
    created_at: datetime


@router.get("/access-history", response_model=list[AuditEventResponse])
def get_access_history(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: UUID | None = None,
) -> list[AuditEventResponse]:
    profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == authenticated.user.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    if profile is None:
        return []
    events = list_profile_access_history(
        session,
        profile_id=profile.id,
        limit=limit,
        before_id=before_id,
    )
    return [AuditEventResponse.model_validate(event, from_attributes=True) for event in events]
