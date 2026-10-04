from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog


def append_audit_event(
    session: Session,
    *,
    action: str,
    actor_user_id: UUID | None,
    target_profile_id: UUID | None = None,
    entity_type: str,
    entity_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        target_profile_id=target_profile_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    session.add(entry)
    session.flush()
    return entry


def list_profile_access_history(
    session: Session,
    *,
    profile_id: UUID,
    limit: int = 100,
    before_id: UUID | None = None,
) -> list[AuditLog]:
    statement = (
        select(AuditLog)
        .where(AuditLog.target_profile_id == profile_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        cursor = session.get(AuditLog, before_id)
        if cursor is None or cursor.target_profile_id != profile_id:
            return []
        statement = statement.where(
            (AuditLog.created_at < cursor.created_at)
            | ((AuditLog.created_at == cursor.created_at) & (AuditLog.id < cursor.id))
        )
    return list(session.scalars(statement))
