from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import AuditLog, SoftDeleteMixin, utc_now

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    def __init__(self, model: type[ModelT]) -> None:
        self.model = model
        self._column_names = {column.key for column in model.__mapper__.column_attrs}
        self._soft_delete = issubclass(model, SoftDeleteMixin)

    def create(self, session: Session, **values: Any) -> ModelT:
        instance = self.model(**values)
        session.add(instance)
        session.flush()
        return instance

    def get(
        self, session: Session, entity_id: UUID, *, include_deleted: bool = False
    ) -> ModelT | None:
        instance = session.get(self.model, entity_id)
        if (
            instance is not None
            and self._soft_delete
            and not include_deleted
            and instance.deleted_at is not None
        ):
            return None
        return instance

    def list(self, session: Session, *, include_deleted: bool = False) -> list[ModelT]:
        statement = select(self.model)
        if self._soft_delete and not include_deleted:
            statement = statement.where(self.model.deleted_at.is_(None))
        return list(session.scalars(statement))

    def update(
        self,
        session: Session,
        entity_id: UUID,
        values: dict[str, Any],
    ) -> ModelT | None:
        if issubclass(self.model, AuditLog):
            raise ValueError("Audit logs are append-only")
        forbidden = {"id", "created_at", "updated_at", "deleted_at"}
        invalid = set(values) - self._column_names | (set(values) & forbidden)
        if invalid:
            raise ValueError(f"Fields cannot be updated: {', '.join(sorted(invalid))}")
        instance = self.get(session, entity_id)
        if instance is None:
            return None
        for field, value in values.items():
            setattr(instance, field, value)
        session.flush()
        return instance

    def delete(self, session: Session, entity_id: UUID) -> bool:
        if issubclass(self.model, AuditLog):
            raise ValueError("Audit logs are append-only")
        instance = self.get(session, entity_id)
        if instance is None:
            return False
        if self._soft_delete:
            instance.deleted_at = utc_now()
        else:
            session.delete(instance)
        session.flush()
        return True
