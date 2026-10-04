from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "backend_api",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.celery_tasks"],
)
celery_app.conf.update(
    beat_schedule={
        "process-due-reminders-every-minute": {
            "task": "app.process_due_reminders",
            "schedule": 60.0,
        },
        "purge-expired-accounts-daily": {
            "task": "app.purge_expired_accounts",
            "schedule": 86400.0,
        },
    },
    timezone="UTC",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
