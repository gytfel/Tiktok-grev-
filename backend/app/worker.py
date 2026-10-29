"""Фоновые процессы на Celery.

Две очереди:
  uploads — загрузка видео (тяжёлые задачи, параллельность задаётся числом процессов воркера);
  default — диспетчер, опрос статусов, проверка аккаунтов.
"""

from functools import lru_cache

from celery import Celery

from app import publishing
from app.accounts_service import check_account
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Account, utcnow
from app.tiktok import TikTokClient

settings = get_settings()

celery_app = Celery("tiktok_queue", broker=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={"app.worker.run_job_task": {"queue": "uploads"}},
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "dispatch-due-jobs": {"task": "app.worker.dispatch_task", "schedule": 5.0},
        "poll-processing-jobs": {"task": "app.worker.poll_dispatch_task", "schedule": 5.0},
        "recover-stale-jobs": {"task": "app.worker.recover_task", "schedule": 60.0},
    },
)


@lru_cache
def tiktok() -> TikTokClient:
    return TikTokClient.from_settings(settings)


@celery_app.task
def dispatch_task() -> int:
    with get_sessionmaker()() as db:
        ids = publishing.dispatch_due_jobs(db, utcnow(), settings.dispatch_batch_size)
    for job_id in ids:
        run_job_task.delay(job_id)
    return len(ids)


@celery_app.task
def run_job_task(job_id: int) -> None:
    with get_sessionmaker()() as db:
        publishing.run_job(db, tiktok(), settings, job_id)


@celery_app.task
def poll_dispatch_task() -> int:
    with get_sessionmaker()() as db:
        ids = publishing.due_poll_job_ids(db, utcnow(), settings.status_poll_interval_s)
    for job_id in ids:
        poll_job_task.delay(job_id)
    return len(ids)


@celery_app.task
def poll_job_task(job_id: int) -> None:
    with get_sessionmaker()() as db:
        publishing.poll_job(db, tiktok(), settings, job_id)


@celery_app.task
def recover_task() -> int:
    with get_sessionmaker()() as db:
        return publishing.recover_stale_jobs(db, utcnow())


@celery_app.task
def check_account_task(account_id: int) -> None:
    with get_sessionmaker()() as db:
        account = db.get(Account, account_id)
        if account is not None:
            check_account(db, account, tiktok(), utcnow())
