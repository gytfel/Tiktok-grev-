from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuditEvent, Reminder, utcnow
from tests.conftest import confirm, connect, make_media
from tests.test_publishing import drive, jobs_of


def iso(dt: datetime) -> str:
    return dt.isoformat()


def window(center: datetime, days: int = 10) -> dict:
    return {"start": iso(center - timedelta(days=days)), "end": iso(center + timedelta(days=days))}


def test_calendar_shows_campaigns_reminders_and_daily_results(
    admin_client, db, fake, tt, settings, clock, admin
):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo")]
    done = confirm(db, admin, make_media(db), accounts, clock)
    drive(db, tt, settings, clock, rounds=3)
    tomorrow = utcnow() + timedelta(days=1)
    planned = confirm(db, admin, make_media(db), accounts, clock, start_at=tomorrow, title="Завтрашняя")
    db.add(Reminder(title="Ответить на комментарии", due_at=clock.now + timedelta(hours=2)))
    db.commit()

    past = admin_client.get("/api/calendar", params=window(clock.now)).json()
    assert [(c["id"], c["status"], c["published"], c["total"], c["planned"]) for c in past["campaigns"]] == [
        (done.id, "completed", 2, 2, False)
    ]
    assert [r["title"] for r in past["reminders"]] == ["Ответить на комментарии"]
    assert past["days"][clock.now.date().isoformat()] == {"published": 2, "problems": 0}

    future = admin_client.get("/api/calendar", params=window(tomorrow)).json()
    assert [(c["id"], c["title"], c["planned"]) for c in future["campaigns"]] == [
        (planned.id, "Завтрашняя", True)
    ]


def test_daily_results_use_requested_time_zone(admin_client, db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    drive(db, tt, settings, clock, rounds=3)
    job = jobs_of(db, campaign)[0]
    job.published_at = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    db.commit()

    utc = admin_client.get("/api/calendar", params=window(clock.now)).json()["days"]
    vladivostok = admin_client.get(
        "/api/calendar", params={**window(clock.now), "tz": "Asia/Vladivostok"}
    ).json()["days"]
    assert utc["2026-01-15"]["published"] == 1 and "2026-01-16" not in utc
    assert vladivostok["2026-01-16"]["published"] == 1


def test_calendar_validates_period_and_zone(admin_client):
    now = utcnow()
    assert (
        admin_client.get(
            "/api/calendar", params={"start": iso(now), "end": iso(now + timedelta(days=90))}
        ).status_code
        == 422
    )
    assert (
        admin_client.get(
            "/api/calendar", params={"start": iso(now), "end": iso(now - timedelta(days=1))}
        ).status_code
        == 422
    )
    naive = {"start": "2026-11-01T00:00:00", "end": "2026-11-30T00:00:00"}
    assert admin_client.get("/api/calendar", params=naive).status_code == 422
    assert admin_client.get("/api/calendar", params={**window(now), "tz": "Mars/Olympus"}).status_code == 422


def test_reschedule_planned_campaign(admin_client, db, fake, tt, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock, start_at=utcnow() + timedelta(days=1))
    new_start = (utcnow() + timedelta(days=3)).replace(microsecond=0)

    r = admin_client.post(f"/api/campaigns/{campaign.id}/reschedule", json={"start_at": iso(new_start)})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert all(j.scheduled_at == new_start for j in jobs_of(db, campaign))
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "campaign.rescheduled"))
    assert event.details["to"] == new_start.isoformat()

    # Время в прошлом — запуск сейчас.
    r = admin_client.post(
        f"/api/campaigns/{campaign.id}/reschedule", json={"start_at": "2020-01-01T00:00:00+00:00"}
    )
    assert r.status_code == 200
    db.expire_all()
    assert abs(jobs_of(db, campaign)[0].scheduled_at - utcnow()) < timedelta(minutes=1)


def test_started_campaign_cannot_be_rescheduled(admin_client, db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    drive(db, tt, settings, clock, rounds=1)
    r = admin_client.post(
        f"/api/campaigns/{campaign.id}/reschedule", json={"start_at": iso(utcnow() + timedelta(days=1))}
    )
    assert r.status_code == 409


def test_reschedule_requires_timezone(admin_client, db, fake, tt, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock, start_at=utcnow() + timedelta(days=1))
    r = admin_client.post(
        f"/api/campaigns/{campaign.id}/reschedule", json={"start_at": "2026-12-01T10:00:00"}
    )
    assert r.status_code == 422


def test_time_zone_preference(admin_client):
    r = admin_client.patch("/api/auth/preferences", json={"timezone": "Asia/Almaty"})
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Almaty"
    assert admin_client.get("/api/auth/me").json()["timezone"] == "Asia/Almaty"
    assert admin_client.patch("/api/auth/preferences", json={"timezone": "Nowhere/City"}).status_code == 422
    assert admin_client.patch("/api/auth/preferences", json={"timezone": None}).json()["timezone"] is None
