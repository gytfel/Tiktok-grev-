import os
import time
from datetime import timedelta

import pytest

from app import monitor, publishing
from app.config import get_settings
from app.deps import get_probes
from app.models import (
    AccountStatus,
    PublicationAttempt,
    PushSubscription,
    Role,
    User,
    utcnow,
)
from app.security import hash_password
from tests.conftest import confirm, connect, make_media
from tests.test_publishing import jobs_of


class FakeProbes:
    def __init__(self, workers=("uploads@srv", "default@srv"), redis_ok=True, beat_age=timedelta(seconds=30)):
        self.names = list(workers)
        self.redis_ok = redis_ok
        self.beat_age = beat_age
        self.disk_free = 60 * 2**30
        self.now = utcnow()

    def redis_ping(self) -> float:
        if not self.redis_ok:
            raise ConnectionError("connection refused")
        return 1.5

    def workers(self) -> list[str]:
        return self.names

    def heartbeat(self):
        return None if self.beat_age is None else self.now - self.beat_age

    def disk(self, path):
        return self.disk_free, 100 * 2**30

    def memory(self):
        return 6 * 2**30, 8 * 2**30

    def load_per_cpu(self):
        return 0.4


def checks(report) -> dict[str, monitor.Check]:
    return {c.key: c for c in report.checks}


def test_healthy_system(db, clock):
    probes = FakeProbes()
    probes.now = clock.now
    report = monitor.collect(db, probes, clock.now)
    by_key = checks(report)
    for key in ("database", "redis", "workers", "scheduler", "queue", "stuck", "errors", "accounts", "push"):
        assert by_key[key].status == monitor.OK, (key, by_key[key])
    # Папка копий не подключена — «нет данных», но общий статус от этого не портится.
    assert by_key["backups"].status == monitor.UNKNOWN
    assert report.status == monitor.OK


def test_missing_worker_and_stale_scheduler(db, clock):
    probes = FakeProbes(workers=["default@srv"], beat_age=timedelta(minutes=10))
    probes.now = clock.now
    report = monitor.collect(db, probes, clock.now)
    by_key = checks(report)
    assert by_key["workers"].status == monitor.CRITICAL and "uploads" in by_key["workers"].value
    assert "загрузка видео" in by_key["workers"].detail
    assert by_key["scheduler"].status == monitor.CRITICAL
    assert report.status == monitor.CRITICAL


def test_redis_down(db, clock):
    report = monitor.collect(db, FakeProbes(redis_ok=False), clock.now)
    by_key = checks(report)
    assert by_key["redis"].status == monitor.CRITICAL
    assert by_key["workers"].status == by_key["scheduler"].status == monitor.UNKNOWN


def test_queue_stalled_versus_busy(db, fake, tt, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo")]
    campaign = confirm(db, admin, make_media(db), accounts, clock)
    probes = FakeProbes()
    clock.advance(minutes=3)
    probes.now = clock.now
    assert checks(monitor.collect(db, probes, clock.now))["queue"].status == monitor.OK

    # Задания ждут, ничего не загружается и загрузки давно не начинались — очередь стоит.
    clock.advance(minutes=10)
    probes.now = clock.now
    queue = checks(monitor.collect(db, probes, clock.now))["queue"]
    assert queue.status == monitor.CRITICAL and queue.value == "стоит: ждут 2"

    # Одна загрузка идёт: длинная очередь — это нормально, она разбирается.
    publishing.claim_job(db, jobs_of(db, campaign)[0].id, clock.now, 600)
    queue = checks(monitor.collect(db, probes, clock.now))["queue"]
    assert queue.status == monitor.OK and "разбирается" in queue.detail


def test_publication_errors_and_reauth(db, fake, tt, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo")]
    campaign = confirm(db, admin, make_media(db), accounts, clock)
    jobs = jobs_of(db, campaign)
    codes = [
        "published",
        "spam_risk_too_many_posts",
        "spam_risk_too_many_posts",
        "internal_error",
        "published",
    ]
    for number, code in enumerate(codes, start=1):
        db.add(
            PublicationAttempt(
                job_id=jobs[number % 2].id,
                attempt_number=number,
                started_at=clock.now,
                finished_at=clock.now,
                result_code=code,
            )
        )
    accounts[1].connection_status = AccountStatus.NEEDS_REAUTH
    db.commit()
    probes = FakeProbes()
    probes.now = clock.now
    by_key = checks(monitor.collect(db, probes, clock.now))
    assert by_key["errors"].status == monitor.WARNING
    assert by_key["errors"].value == "3 из 5 попыток"
    assert by_key["errors"].detail.startswith("spam_risk_too_many_posts: 2")
    assert by_key["accounts"].status == monitor.WARNING and by_key["accounts"].value == "нужен вход: 1"


def test_backup_age(db, clock, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "backup_interval_hours", 24)
    probes = FakeProbes()
    now = utcnow()
    probes.now = now
    assert checks(monitor.collect(db, probes, now))["backups"].value == "копий нет"
    dump = tmp_path / "db-20260101-000000.dump"
    dump.write_bytes(b"x")
    assert checks(monitor.collect(db, probes, now))["backups"].status == monitor.OK
    old = time.time() - 40.5 * 3600
    os.utime(dump, (old, old))
    backups = checks(monitor.collect(db, probes, now))["backups"]
    assert backups.status == monitor.WARNING and backups.value == "последняя 40 ч назад"
    old = time.time() - 4 * 24 * 3600
    os.utime(dump, (old, old))
    assert checks(monitor.collect(db, probes, now))["backups"].status == monitor.CRITICAL


def test_low_disk(db, clock):
    probes = FakeProbes()
    probes.now = clock.now
    probes.disk_free = 10 * 2**30
    disk = checks(monitor.collect(db, probes, clock.now))["disk"]
    assert disk.status == monitor.WARNING and disk.value == "свободно 10% (10.0 ГБ)"
    probes.disk_free = 3 * 2**30
    assert checks(monitor.collect(db, probes, clock.now))["disk"].status == monitor.CRITICAL


def test_alerts_need_two_checks_and_report_recovery(db, clock):
    alerter = monitor.Alerter()
    broken = FakeProbes(workers=["default@srv"])
    broken.now = clock.now
    healthy = FakeProbes()
    healthy.now = clock.now

    assert alerter.step(monitor.collect(db, broken, clock.now)) == []  # одиночный сбой — не повод будить
    [alert] = alerter.step(monitor.collect(db, broken, clock.now))
    assert alert.title == "Сбой: Воркеры" and "uploads" in alert.body and alert.url == "/system"
    assert alerter.step(monitor.collect(db, broken, clock.now)) == []  # без повторов
    [recovered] = alerter.step(monitor.collect(db, healthy, clock.now))
    assert recovered.title == "Система снова в норме" and recovered.body == "Воркеры"
    assert alerter.step(monitor.collect(db, healthy, clock.now)) == []

    # Две проблемы, одна уходит: сообщается, что восстановлено и что ещё осталось.
    broken.disk_free = 3 * 2**30
    alerter.step(monitor.collect(db, broken, clock.now))
    [alert] = alerter.step(monitor.collect(db, broken, clock.now))
    assert alert.title == "Сбой: проблем — 2"
    healthy.disk_free = 3 * 2**30
    [partial] = alerter.step(monitor.collect(db, healthy, clock.now))
    assert partial.title == "Восстановлено: Воркеры" and partial.body == "Ещё не решено: Диск"


def test_alert_recipients(db, admin):
    viewer = User(username="viewer", password_hash=hash_password("viewer-password"), role=Role.VIEWER)
    db.add(viewer)
    db.flush()
    for n, (user, prefs) in enumerate(((admin, {}), (admin, {"system": False}), (viewer, {}))):
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=f"https://fcm.googleapis.com/fcm/send/{n}",
                p256dh="k",
                auth="a",
                preferences=prefs,
            )
        )
    db.commit()
    assert [s.endpoint[-1] for s in monitor.alert_recipients(db)] == ["0"]


@pytest.fixture
def probes():
    from app.main import app

    fake = FakeProbes()
    app.dependency_overrides[get_probes] = lambda: fake
    return fake


def test_status_api(admin_client, client, db, probes):
    r = admin_client.get("/api/system/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok" and data["metrics_enabled"] is False
    assert {c["key"] for c in data["checks"]} >= {"database", "workers", "queue", "backups"}

    db.add(User(username="viewer", password_hash=hash_password("viewer-password"), role=Role.VIEWER))
    db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert client.get("/api/system/status").status_code == 403


def test_metrics_endpoint(client, probes, monkeypatch):
    assert client.get("/api/metrics").status_code == 404
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret-token")
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/metrics", headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "tiktok_status 0" in r.text
    assert 'tiktok_check_status{check="workers"} 0' in r.text
    assert 'tiktok_jobs{status="queued"} 0' in r.text
    assert "tiktok_workers_up 2" in r.text
