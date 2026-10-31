"""Фоновые процессы на Celery.

Две очереди:
  uploads — загрузка видео (тяжёлые задачи, параллельность задаётся числом процессов воркера);
  default — диспетчер, опрос статусов, проверка аккаунтов.
"""

from functools import lru_cache

from celery import Celery
from celery.signals import beat_init

from app import analytics, monitor, notifications, publishing, push
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
        "notify": {"task": "app.worker.notify_task", "schedule": float(settings.notify_interval_s)},
        "collect-analytics": {
            "task": "app.worker.analytics_dispatch_task",
            "schedule": float(settings.analytics_interval_hours * 3600),
        },
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


@lru_cache
def probes() -> monitor.Probes:
    return monitor.Probes(settings)


@beat_init.connect
def _beat_started(**_kwargs) -> None:
    # Первая плановая отметка появится только через минуту — без этой монитор показал бы сбой.
    monitor.record_heartbeat(probes(), utcnow())


@celery_app.task
def recover_task() -> int:
    # Задача запускается планировщиком раз в минуту — заодно оставляет отметку для монитора.
    monitor.record_heartbeat(probes(), utcnow())
    with get_sessionmaker()() as db:
        return publishing.recover_stale_jobs(db, utcnow())


@celery_app.task
def check_account_task(account_id: int) -> None:
    with get_sessionmaker()() as db:
        account = db.get(Account, account_id)
        if account is not None:
            check_account(db, account, tiktok(), utcnow())


@celery_app.task
def notify_task() -> int:
    with get_sessionmaker()() as db:
        messages = notifications.collect(db, utcnow())
        sent = 0
        for event, message in messages:
            sent += push.send(db, message, push.subscriptions_for(db, event))
    return sent


@celery_app.task
def analytics_dispatch_task() -> int:
    if not settings.tiktok_analytics:
        return 0
    with get_sessionmaker()() as db:
        ids = analytics.eligible_account_ids(db)
    for account_id in ids:
        collect_analytics_task.delay(account_id)
    return len(ids)


@celery_app.task
def collect_analytics_task(account_id: int) -> None:
    with get_sessionmaker()() as db:
        analytics.collect_account(db, tiktok(), account_id, utcnow())
