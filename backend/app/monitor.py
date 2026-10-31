"""Монитор системы: база, Redis, воркеры, планировщик, очередь, диск, память и резервные копии.

Проверки собираются по запросу страницы «Система» и раз в минуту фоновым циклом панели. Цикл
присылает push-уведомление, когда проблема держится две проверки подряд, и когда она уходит.
Он работает в процессе панели, а не в воркере: иначе об остановке воркеров некому было бы сообщить.
"""

import asyncio
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    Account,
    AccountStatus,
    JobStatus,
    PublicationAttempt,
    PublicationJob,
    PushSubscription,
    Role,
    User,
)
from app.push import PushMessage

log = logging.getLogger(__name__)

OK, UNKNOWN, WARNING, CRITICAL = "ok", "unknown", "warning", "critical"
SEVERITY = {OK: 0, UNKNOWN: 1, WARNING: 2, CRITICAL: 3}
WORKERS = {"uploads": "загрузка видео", "default": "планировщик и опрос статусов"}
HEARTBEAT_KEY = "monitor:heartbeat"
SUCCESS_CODES = ("published", "sent_to_inbox")
# Сколько задание может ждать выдачи воркеру: диспетчер запускается каждые 5 секунд.
OVERDUE_AFTER = timedelta(minutes=5)
STALL_AFTER = timedelta(minutes=10)
HEARTBEAT_STALE = timedelta(minutes=3)


@dataclass
class Check:
    key: str
    title: str
    status: str
    value: str
    detail: str = ""


@dataclass
class Report:
    status: str
    checks: list[Check]
    jobs: dict[str, int]
    metrics: dict[str, float] = field(default_factory=dict)

    def as_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "status": self.status,
            "generated_at": now,
            "checks": [asdict(c) for c in self.checks],
            "jobs": self.jobs,
        }


class Probes:
    """Redis, Celery и сам сервер. В тестах подменяются, чтобы не зависеть от живых сервисов и машины."""

    def __init__(self, settings: Settings):
        from redis import Redis

        self.redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)

    def redis_ping(self) -> float:
        started = time.monotonic()
        self.redis.ping()
        return (time.monotonic() - started) * 1000

    def workers(self) -> list[str]:
        from app.worker import celery_app

        replies = celery_app.control.ping(timeout=1.0)
        return sorted(name for reply in replies for name in reply)

    def heartbeat(self) -> datetime | None:
        value = self.redis.get(HEARTBEAT_KEY)
        return datetime.fromisoformat(value.decode()) if value else None

    def disk(self, path: str) -> tuple[int, int]:
        Path(path).mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        return usage.free, usage.total

    def memory(self) -> tuple[int, int] | None:
        """Доступная и общая память, байт (Linux)."""
        try:
            lines = Path("/proc/meminfo").read_text().splitlines()
        except OSError:
            return None
        values = {}
        for line in lines:
            name, _, rest = line.partition(":")
            values[name] = int(rest.split()[0]) * 1024
        if "MemTotal" not in values or "MemAvailable" not in values:
            return None
        return values["MemAvailable"], values["MemTotal"]

    def load_per_cpu(self) -> float | None:
        if not hasattr(os, "getloadavg"):
            return None
        return os.getloadavg()[0] / (os.cpu_count() or 1)


def record_heartbeat(probes: Probes, now: datetime) -> None:
    """Отметка планировщика: её ставит задача, которую он запускает раз в минуту."""
    probes.redis.set(HEARTBEAT_KEY, now.isoformat(), ex=3600)


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "меньше минуты"
    if minutes < 120:
        return f"{minutes} мин"
    hours = minutes // 60
    return f"{hours} ч" if hours < 48 else f"{hours // 24} дн."


def _percent(ratio: float) -> str:
    return f"{ratio * 100:.0f}%"


def _newest_backup(directory: str) -> datetime | None:
    dumps = list(Path(directory).glob("db-*.dump"))
    if not dumps:
        return None
    return datetime.fromtimestamp(max(p.stat().st_mtime for p in dumps), UTC)


def collect(db: Session, probes: Probes, now: datetime, settings: Settings | None = None) -> Report:
    settings = settings or get_settings()
    checks: list[Check] = []
    metrics: dict[str, float] = {}

    # База данных.
    started = time.monotonic()
    try:
        db.execute(text("SELECT 1"))
        ms = (time.monotonic() - started) * 1000
        checks.append(Check("database", "База данных", OK, f"отвечает за {ms:.0f} мс"))
    except Exception as exc:  # noqa: BLE001 — любая ошибка базы означает сбой
        db.rollback()
        checks.append(Check("database", "База данных", CRITICAL, "не отвечает", str(exc)[:200]))
        return Report(CRITICAL, checks, {}, {"check_database": SEVERITY[CRITICAL]})

    # Redis, воркеры и планировщик: без Redis воркеры не получают задания.
    redis_ok = True
    try:
        ms = probes.redis_ping()
        checks.append(Check("redis", "Redis (очередь задач)", OK, f"отвечает за {ms:.0f} мс"))
    except Exception as exc:  # noqa: BLE001
        redis_ok = False
        checks.append(
            Check("redis", "Redis (очередь задач)", CRITICAL, "не отвечает", "Воркеры не получают задания.")
        )
        log.warning("Redis не отвечает: %s", exc)

    if redis_ok:
        try:
            names = probes.workers()
        except Exception as exc:  # noqa: BLE001
            names = []
            log.warning("Не удалось опросить воркеры: %s", exc)
        alive = {name.split("@", 1)[0] for name in names}
        missing = [queue for queue in WORKERS if queue not in alive]
        metrics["workers_up"] = len(names)
        if missing:
            checks.append(
                Check(
                    "workers",
                    "Воркеры",
                    CRITICAL,
                    "не отвечают: " + ", ".join(missing),
                    "Не работает: "
                    + "; ".join(WORKERS[q] for q in missing)
                    + ". Проверьте: docker compose ps и docker compose logs worker worker-uploads.",
                )
            )
        else:
            checks.append(Check("workers", "Воркеры", OK, f"работают: {len(names)}", ", ".join(names)))

        beat = probes.heartbeat()
        if beat is None or now - beat > HEARTBEAT_STALE:
            checks.append(
                Check(
                    "scheduler",
                    "Планировщик",
                    CRITICAL,
                    "нет отметки" if beat is None else f"последняя отметка {_ago(now - beat)} назад",
                    "Задания не выдаются воркерам по расписанию. Планировщик работает в сервисе worker.",
                )
            )
        else:
            checks.append(Check("scheduler", "Планировщик", OK, f"отметка {_ago(now - beat)} назад"))
        if beat is not None:
            metrics["scheduler_heartbeat_age_seconds"] = (now - beat).total_seconds()
    else:
        checks.append(Check("workers", "Воркеры", UNKNOWN, "нельзя проверить без Redis"))
        checks.append(Check("scheduler", "Планировщик", UNKNOWN, "нельзя проверить без Redis"))

    # Очередь заданий.
    jobs = dict(db.execute(select(PublicationJob.status, func.count()).group_by(PublicationJob.status)).all())
    # Большая кампания разбирается долго, и это нормально. Сбой — когда задания ждут,
    # а загрузки не идут: ничего не выполняется и ни одна попытка не начиналась 10 минут.
    overdue_filter = (
        PublicationJob.status == JobStatus.QUEUED,
        PublicationJob.scheduled_at < now - OVERDUE_AFTER,
    )
    overdue = db.scalar(select(func.count()).where(*overdue_filter)) or 0
    oldest = db.scalar(select(func.min(PublicationJob.scheduled_at)).where(*overdue_filter))
    waiting = oldest and now - oldest
    in_work = jobs.get(JobStatus.UPLOADING, 0) + jobs.get(JobStatus.PROCESSING, 0)
    last_start = db.scalar(select(func.max(PublicationAttempt.started_at)))
    stalled = bool(overdue) and not in_work and (last_start is None or now - last_start > STALL_AFTER)
    metrics["queue_overdue_jobs"] = overdue
    metrics["queue_oldest_overdue_seconds"] = waiting.total_seconds() if waiting else 0
    metrics["queue_stalled"] = int(stalled)
    queued = jobs.get(JobStatus.QUEUED, 0)
    if stalled:
        checks.append(
            Check(
                "queue",
                "Очередь",
                CRITICAL,
                f"стоит: ждут {overdue}",
                f"Самое старое задание ждёт {_ago(waiting)}, а загрузки не начинаются. "
                "Проверьте воркеры и планировщик.",
            )
        )
    elif overdue:
        checks.append(
            Check(
                "queue",
                "Очередь",
                OK,
                f"в очереди: {queued}",
                f"Загрузки идут, очередь разбирается. Самое старое задание ждёт {_ago(waiting)}.",
            )
        )
    else:
        checks.append(Check("queue", "Очередь", OK, f"в очереди: {queued}"))

    # Зависшие задания: восстановление и опрос статусов должны были их подхватить.
    stale = now - OVERDUE_AFTER
    stuck = (
        db.scalar(
            select(func.count()).where(
                ((PublicationJob.status == JobStatus.UPLOADING) & (PublicationJob.lease_until < stale))
                | ((PublicationJob.status == JobStatus.PROCESSING) & (PublicationJob.next_poll_at < stale))
            )
        )
        or 0
    )
    metrics["stuck_jobs"] = stuck
    if stuck:
        checks.append(
            Check(
                "stuck",
                "Зависшие задания",
                WARNING,
                f"{stuck} без движения",
                "Загрузка или проверка статуса не продолжается. Обычно это значит, что воркер остановлен.",
            )
        )
    else:
        checks.append(Check("stuck", "Зависшие задания", OK, f"нет · в работе: {in_work}"))

    # Ответы TikTok API за сутки.
    since = now - timedelta(hours=24)
    by_code = db.execute(
        select(PublicationAttempt.result_code, func.count())
        .where(PublicationAttempt.finished_at >= since)
        .group_by(PublicationAttempt.result_code)
    ).all()
    errors = sorted(((c or "без кода", n) for c, n in by_code if c not in SUCCESS_CODES), key=lambda r: -r[1])
    failed, total = sum(n for _, n in errors), sum(n for _, n in by_code)
    metrics["attempts_24h"] = total
    metrics["attempt_errors_24h"] = failed
    status = OK
    if total and failed >= 5 and failed / total >= 0.5:
        status = CRITICAL
    elif total and failed >= 3 and failed / total >= 0.2:
        status = WARNING
    checks.append(
        Check(
            "errors",
            "Ошибки публикации за 24 ч",
            status,
            f"{failed} из {total} попыток" if total else "попыток не было",
            ", ".join(f"{code}: {n}" for code, n in errors[:3]),
        )
    )

    # Аккаунты, которым нужен повторный вход.
    accounts = dict(
        db.execute(select(Account.connection_status, func.count()).group_by(Account.connection_status)).all()
    )
    reauth = accounts.get(AccountStatus.NEEDS_REAUTH, 0)
    metrics.update({f"accounts_{k}": v for k, v in accounts.items()})
    checks.append(
        Check(
            "accounts",
            "Аккаунты",
            WARNING if reauth else OK,
            f"нужен вход: {reauth}" if reauth else f"подключено: {accounts.get(AccountStatus.CONNECTED, 0)}",
            "Переподключите их на странице «Аккаунты»." if reauth else "",
        )
    )

    # Место на диске с видео.
    try:
        free_bytes, total_bytes = probes.disk(settings.media_dir)
        free = free_bytes / total_bytes
        metrics["disk_free_ratio"] = round(free, 4)
        checks.append(
            Check(
                "disk",
                "Диск",
                CRITICAL if free < 0.05 else WARNING if free < 0.15 else OK,
                f"свободно {_percent(free)} ({free_bytes / 2**30:.1f} ГБ)",
                "Загрузка новых видео может прерваться. Удалите старые файлы или увеличьте диск."
                if free < 0.15
                else "",
            )
        )
    except OSError as exc:
        checks.append(Check("disk", "Диск", UNKNOWN, "нет данных", str(exc)[:200]))

    # Память и нагрузка процессора.
    memory = probes.memory()
    if memory:
        available, total_mem = memory
        ratio = available / total_mem
        metrics["memory_available_ratio"] = round(ratio, 4)
        checks.append(
            Check(
                "memory",
                "Память",
                CRITICAL if ratio < 0.05 else WARNING if ratio < 0.1 else OK,
                f"доступно {_percent(ratio)} из {total_mem / 2**30:.1f} ГБ",
            )
        )
    else:
        checks.append(Check("memory", "Память", UNKNOWN, "нет данных"))
    per_cpu = probes.load_per_cpu()
    if per_cpu is not None:
        metrics["load_per_cpu"] = round(per_cpu, 3)
        checks.append(
            Check(
                "cpu",
                "Процессор",
                WARNING if per_cpu > 2 else OK,
                f"нагрузка {per_cpu:.2f} на ядро",
                "Сервер перегружен: загрузки могут идти медленнее." if per_cpu > 2 else "",
            )
        )

    # Резервные копии.
    if settings.backup_dir and Path(settings.backup_dir).is_dir():
        newest = _newest_backup(settings.backup_dir)
        interval = timedelta(hours=settings.backup_interval_hours)
        if newest is None:
            checks.append(
                Check("backups", "Резервные копии", WARNING, "копий нет", "Проверьте сервис backup.")
            )
        else:
            age = now - newest
            metrics["backup_age_seconds"] = age.total_seconds()
            checks.append(
                Check(
                    "backups",
                    "Резервные копии",
                    CRITICAL if age > interval * 3 else WARNING if age > interval * 1.5 else OK,
                    f"последняя {_ago(age)} назад",
                    "Новые копии не создаются: docker compose logs backup." if age > interval * 1.5 else "",
                )
            )
    else:
        checks.append(
            Check("backups", "Резервные копии", UNKNOWN, "папка не подключена", "Укажите BACKUP_DIR.")
        )

    # Push-подписки.
    subs = db.execute(
        select(func.count(), func.count(PushSubscription.last_error)).select_from(PushSubscription)
    ).one()
    checks.append(
        Check(
            "push",
            "Push-уведомления",
            WARNING if subs[1] else OK,
            f"устройств: {subs[0]}" + (f", с ошибкой: {subs[1]}" if subs[1] else ""),
            "Последняя отправка на эти устройства не удалась." if subs[1] else "",
        )
    )

    for check in checks:
        metrics[f"check_{check.key}"] = SEVERITY[check.status]
    # «Нет данных» само по себе не проблема: общий статус решают предупреждения и сбои.
    overall = max((c.status for c in checks if c.status != UNKNOWN), key=SEVERITY.__getitem__, default=OK)
    return Report(overall, checks, jobs, metrics)


def prometheus(report: Report) -> str:
    """Метрики в текстовом формате Prometheus."""
    lines = [
        "# HELP tiktok_status Overall status: 0 ok, 1 unknown, 2 warning, 3 critical.",
        "# TYPE tiktok_status gauge",
        f"tiktok_status {SEVERITY[report.status]}",
        "# HELP tiktok_check_status Check status: 0 ok, 1 unknown, 2 warning, 3 critical.",
        "# TYPE tiktok_check_status gauge",
    ]
    lines += [f'tiktok_check_status{{check="{c.key}"}} {SEVERITY[c.status]}' for c in report.checks]
    lines += ["# HELP tiktok_jobs Publication jobs by status.", "# TYPE tiktok_jobs gauge"]
    statuses = sorted(set(report.jobs) | set(JobStatus.PENDING) | set(JobStatus.TERMINAL))
    lines += [f'tiktok_jobs{{status="{s}"}} {report.jobs.get(s, 0)}' for s in statuses]
    accounts = {
        k.removeprefix("accounts_"): v for k, v in report.metrics.items() if k.startswith("accounts_")
    }
    lines += ["# HELP tiktok_accounts Accounts by connection status.", "# TYPE tiktok_accounts gauge"]
    lines += [f'tiktok_accounts{{status="{s}"}} {n}' for s, n in sorted(accounts.items())]
    for name, value in sorted(report.metrics.items()):
        if name.startswith(("check_", "accounts_")):
            continue
        lines += [f"# TYPE tiktok_{name} gauge", f"tiktok_{name} {value:g}"]
    return "\n".join(lines) + "\n"


# --- уведомления о сбоях ------------------------------------------------------------------


class Alerter:
    """Помнит, о каких проблемах уже сообщено. Проблема должна продержаться две проверки подряд:
    короткий перезапуск воркеров при обновлении не должен будить людей."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.alerted: dict[str, str] = {}

    def step(self, report: Report) -> list[PushMessage]:
        problems = {c.key: c for c in report.checks if c.status in (WARNING, CRITICAL)}
        confirmed = {k: c for k, c in problems.items() if self.seen.get(k) == c.status}
        new = [c for k, c in confirmed.items() if self.alerted.get(k) != c.status]
        resolved = [c for c in report.checks if c.key in self.alerted and c.key not in problems]
        self.seen = {k: c.status for k, c in problems.items()}
        for check in new:
            self.alerted[check.key] = check.status
        for check in resolved:
            del self.alerted[check.key]

        messages = []
        if new:
            new.sort(key=lambda c: -SEVERITY[c.status])
            head = "Сбой" if new[0].status == CRITICAL else "Предупреждение"
            title = f"{head}: {new[0].title}" if len(new) == 1 else f"{head}: проблем — {len(new)}"
            body = "; ".join(f"{c.title}: {c.value}" for c in new)
            messages.append(PushMessage(title=title, body=body[:300], url="/system", tag="system"))
        if resolved:
            fixed = ", ".join(c.title for c in resolved)
            if self.alerted:
                titles = {c.key: c.title for c in report.checks}
                remaining = ", ".join(titles.get(k, k) for k in self.alerted)
                title, body = f"Восстановлено: {fixed}", f"Ещё не решено: {remaining}"
            else:
                title, body = "Система снова в норме", fixed
            messages.append(PushMessage(title=title, body=body, url="/system", tag="system"))
        return messages


def alert_recipients(db: Session) -> list[PushSubscription]:
    subs = db.scalars(
        select(PushSubscription)
        .join(User, User.id == PushSubscription.user_id)
        .where(User.role.in_((Role.ADMIN, Role.OPERATOR)))
    ).all()
    return [s for s in subs if (s.preferences or {}).get("system", True)]


def run_cycle(alerter: Alerter, probes: Probes, now: datetime) -> int:
    from app import push
    from app.db import get_sessionmaker

    with get_sessionmaker()() as db:
        report = collect(db, probes, now)
        sent = 0
        for message in alerter.step(report):
            # В журнал — всегда: так о сбое видно в docker compose logs app, даже без push.
            log.warning("Монитор: %s — %s", message.title, message.body)
            sent += push.send(db, message, alert_recipients(db))
    return sent


async def watch(interval_s: float) -> None:
    """Фоновый цикл панели: проверки раз в interval_s секунд и уведомления о сбоях."""
    from app.models import utcnow

    alerter = Alerter()
    probes = Probes(get_settings())
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(run_cycle, alerter, probes, utcnow())
        except Exception:  # noqa: BLE001 — монитор не должен падать вместе с проверяемым
            log.exception("Ошибка монитора системы")
