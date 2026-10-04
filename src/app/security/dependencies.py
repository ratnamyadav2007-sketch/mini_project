from collections.abc import Callable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.routes.auth.dependencies import (
    AuthenticatedSession,
    get_authenticated_session,
)
from app.db.base import Base
from app.db.models import HealthRecord, User, VisibilityMixin
from app.db.session import get_db
from app.security.audit import append_audit_event
from app.security.permissions import Action, evaluate_access

ModelT = TypeVar("ModelT", bound=Base)
DbSession = Annotated[Session, Depends(get_db)]
CurrentAuthenticatedSession = Annotated[AuthenticatedSession, Depends(get_authenticated_session)]


def get_current_user(authenticated: CurrentAuthenticatedSession) -> User:
    return authenticated.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_record_access(
    model: type[ModelT],
    resource: str,
    action: Action,
) -> Callable[..., ModelT]:
    if not hasattr(model, "profile_id") or not issubclass(model, VisibilityMixin):
        raise TypeError(f"{model.__name__} does not support profile-scoped visibility")

    def dependency(
        record_id: UUID,
        authenticated: CurrentAuthenticatedSession,
        session: DbSession,
    ) -> ModelT:
        record = session.get(model, record_id)
        if record is not None and hasattr(record, "deleted_at") and record.deleted_at is not None:
            record = None
        if record is None:
            append_audit_event(
                session,
                action="access_denied",
                actor_user_id=authenticated.user.id,
                entity_type=resource,
                entity_id=record_id,
                details={"action": action.value, "reason": "record_not_found"},
            )
            session.commit()
            raise HTTPException(status_code=404, detail="Record not found")

        decision = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=record.profile_id,
            visibility=record.visibility,
            action=action,
            record_id=record.id,
            resource=resource,
        )
        append_audit_event(
            session,
            action="record_access_allowed" if decision.allowed else "record_access_denied",
            actor_user_id=authenticated.user.id,
            target_profile_id=record.profile_id,
            entity_type=resource,
            entity_id=record.id,
            details={
                "action": action.value,
                "visibility": record.visibility,
                "reason": decision.reason,
            },
        )
        session.commit()
        if not decision.allowed:
            raise HTTPException(status_code=403, detail="Access to this record is denied")
        return record

    return dependency


def require_health_record_access(action: Action) -> Callable[..., HealthRecord]:
    return require_record_access(HealthRecord, "health_records", action)
