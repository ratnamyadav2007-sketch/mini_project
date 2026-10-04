from app.celery_app import celery_app
from app.tasks import run_account_purge, run_reminder_scheduler


@celery_app.task(name="app.process_due_reminders")
def process_due_reminders_task() -> int:
    return run_reminder_scheduler()


@celery_app.task(name="app.purge_expired_accounts")
def purge_expired_accounts_task() -> int:
    return run_account_purge()
