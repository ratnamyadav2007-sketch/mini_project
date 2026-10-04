import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import NotificationOutbox, SOSEvent

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class NotificationNotDeliverable(Exception):
    pass


class Notifier(Protocol):
    def deliver(self, recipient: str, message: str) -> None: ...


class DemoLogNotifier:
    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path)

    def deliver(self, recipient: str, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "delivered_at": datetime.now(UTC).isoformat(),
            "mode": "demo",
            "recipient": recipient,
            "message": message,
        }
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, separators=(",", ":")) + "\n")
        logger.info("Demo notification delivered to %s", recipient)


def _outbox_message(session: Session, item: NotificationOutbox) -> str:
    if item.sos_event_id is not None:
        event = session.get(SOSEvent, item.sos_event_id)
        if (
            event is None
            or event.public_expires_at is None
            or event.public_token_hash is None
            or event.resolved_at is not None
            or event.deleted_at is not None
            or _utc(event.public_expires_at) <= datetime.now(UTC)
        ):
            raise NotificationNotDeliverable("SOS event is closed or its link has expired")
        from app.api.v1.routes.sos import create_public_sos_token

        token = create_public_sos_token(event.id, event.public_expires_at)
        return f"Emergency health card: {item.payload['base_url']}/public-sos.html?token={token}"
    if item.reminder_id is not None:
        return str(item.payload["message"])
    raise RuntimeError("Notification outbox entry has no supported event")


def dispatch_pending_notifications(
    session: Session,
    *,
    notifier: Notifier | None = None,
    limit: int = 100,
) -> list[NotificationOutbox]:
    if not 1 <= limit <= 1000:
        raise ValueError("Notification batch limit must be between 1 and 1000")
    notifier = notifier or DemoLogNotifier(get_settings().notification_log_path)
    pending = list(
        session.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.status == "pending")
            .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    completed: list[NotificationOutbox] = []
    for item in pending:
        item.attempts += 1
        try:
            notifier.deliver(item.recipient, _outbox_message(session, item))
        except OSError as exception:
            item.last_error = str(exception)[:255]
            logger.error("Unable to deliver demo notification %s: %s", item.id, exception)
        except NotificationNotDeliverable as exception:
            item.status = "cancelled"
            item.last_error = str(exception)[:255]
        else:
            item.status = "delivered"
            item.delivered_at = datetime.now(UTC)
            item.last_error = None
            completed.append(item)
    session.commit()
    return completed


def enqueue_sos_notifications(
    session: Session,
    *,
    event_id: UUID,
    recipients: list[str],
    base_url: str,
) -> list[NotificationOutbox]:
    items = [
        NotificationOutbox(
            sos_event_id=event_id,
            recipient=recipient,
            payload={"base_url": base_url.rstrip("/")},
            status="pending",
        )
        for recipient in recipients
    ]
    session.add_all(items)
    session.flush()
    return items


def enqueue_reminder_notification(
    session: Session,
    *,
    reminder_id: UUID,
    recipient: str,
    message: str,
) -> NotificationOutbox:
    item = NotificationOutbox(
        reminder_id=reminder_id,
        recipient=recipient,
        payload={"message": message},
    )
    session.add(item)
    session.flush()
    return item
