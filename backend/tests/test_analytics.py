from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import analytics
from app.accounts_service import save_tokens
from app.config import get_settings
from app.deps import get_analytics_enqueuer
from app.models import (
    Account,
    AccountStatsDaily,
    AccountStatus,
    CampaignStatsDaily,
)
from app.schemas import PostSettings
from tests.conftest import confirm, connect, make_media
from tests.test_publishing import drive, jobs_of

ALL_SCOPES = "user.info.basic,video.publish,user.info.stats,video.list"


@pytest.fixture
def analytics_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "tiktok_analytics", True)


def connect_with_stats(db, fake, tt, username, clock) -> Account:
    open_id = f"open-{username}"
    if open_id not in fake.creators:
        fake.add_creator(username)
    ts = tt.exchange_code(fake.issue_code(open_id, ALL_SCOPES), get_settings().oauth_redirect_uri)
    account = Account(label=username, open_id=open_id, connection_status=AccountStatus.CONNECTED)
    db.add(account)
    save_tokens(db, account, ts, clock.now)
    db.commit()
    return account


def test_oauth_requests_analytics_scopes_only_when_enabled(analytics_on, admin_client):
    assert get_settings().oauth_scopes == ALL_SCOPES
    r = admin_client.get("/api/oauth/tiktok/start", follow_redirects=False)
    assert "user.info.stats" in r.headers["location"] and "video.list" in r.headers["location"]


def test_scopes_without_analytics():
    assert get_settings().oauth_scopes == "user.info.basic,video.publish"


def test_collects_video_and_account_stats(analytics_on, db, fake, tt, settings, clock, admin):
    a, b = (connect_with_stats(db, fake, tt, n, clock) for n in ("alpha", "bravo"))
    campaign = confirm(db, admin, make_media(db), [a, b], clock)
    drive(db, tt, settings, clock, rounds=3)
    clock.advance(hours=5)

    for account in (a, b):
        assert analytics.collect_account(db, tt, account.id, clock.now) == {campaign.id}
    db.expire_all()

    jobs = jobs_of(db, campaign)
    assert all(j.views and j.likes is not None and j.stats_updated_at for j in jobs)
    snapshot = db.scalar(select(CampaignStatsDaily))
    assert snapshot.day == clock.now.date()
    assert snapshot.views == sum(j.views for j in jobs) and snapshot.videos == 2
    first_views = snapshot.views
    followers = {r.account_id: r.followers for r in db.scalars(select(AccountStatsDaily))}
    assert set(followers) == {a.id, b.id} and all(v > 0 for v in followers.values())

    # Повторный сбор в тот же день обновляет срез, а не создаёт второй.
    clock.advance(hours=1)
    analytics.collect_account(db, tt, a.id, clock.now)
    db.expire_all()
    assert len(db.scalars(select(CampaignStatsDaily)).all()) == 1
    assert db.scalar(select(CampaignStatsDaily)).views > first_views


def test_accounts_without_analytics_scopes_are_skipped(analytics_on, db, fake, tt, clock):
    account = connect(db, fake, tt, "alpha", clock)
    assert analytics.eligible_account_ids(db) == []
    assert analytics.collect_account(db, tt, account.id, clock.now) == set()
    assert not any(path.startswith("/v2/user/info") for _, path in fake.calls)


def test_private_videos_have_no_stats(analytics_on, db, fake, tt, settings, clock, admin):
    account = connect_with_stats(db, fake, tt, "alpha", clock)
    confirm(db, admin, make_media(db), [account], clock, settings=PostSettings(privacy_level="SELF_ONLY"))
    drive(db, tt, settings, clock, rounds=3)
    assert analytics.collect_account(db, tt, account.id, clock.now) == set()
    data = analytics.overview(db, 7, clock.now)
    assert data["videos_private"] == 1 and data["videos_tracked"] == 0


def test_overview_series_and_deltas(db, fake, tt, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    today = clock.now.date()
    # Срезы: 10 дней назад (до окна), затем пропуск, затем три дня внутри окна.
    for offset, views in ((10, 100), (2, 400), (1, 700), (0, 1000)):
        db.add(
            CampaignStatsDaily(
                campaign_id=campaign.id, day=today - timedelta(days=offset), views=views, videos=1
            )
        )
    # Аккаунт подключили два дня назад: прирост считается от первого значения, а не от нуля.
    for offset, followers in ((2, 5000), (0, 5300)):
        db.add(
            AccountStatsDaily(account_id=account.id, day=today - timedelta(days=offset), followers=followers)
        )
    db.commit()

    data = analytics.overview(db, 7, clock.now)
    assert data["totals"]["views"] == 1000 and data["deltas"]["views"] == 900
    assert data["totals"]["followers"] == 5300 and data["deltas"]["followers"] == 300
    by_day = {p["day"]: p for p in data["series"]}
    assert len(data["series"]) == 7
    assert by_day[(today - timedelta(days=6)).isoformat()]["views"] == 100  # перенос значения до окна
    assert by_day[(today - timedelta(days=2)).isoformat()]["views_gain"] == 300
    assert (
        by_day[(today - timedelta(days=1)).isoformat()]["followers"] == 5000
    )  # пропуск — последнее известное
    assert data["top_accounts"][0]["followers_gain"] == 300


def test_series_before_first_snapshot(db, fake, tt, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    media = make_media(db)
    today = clock.now.date()
    fresh = confirm(db, admin, media, [account], clock)
    old = confirm(db, admin, media, [account], clock, allow_duplicates=True)
    # Время старта в прошлом при подтверждении не принимается — выставляем напрямую.
    fresh.start_at = clock.now - timedelta(days=3)
    old.start_at = clock.now - timedelta(days=60)
    launch_day = today - timedelta(days=3)
    for campaign, views in ((fresh, 300), (old, 5000)):
        db.add(CampaignStatsDaily(campaign_id=campaign.id, day=launch_day, views=views, videos=1))
    # Аккаунт подключили 3 дня назад: до этого подписчиков не «ноль», а неизвестно.
    db.add(AccountStatsDaily(account_id=account.id, day=launch_day, followers=1000))
    db.commit()

    by_day = {p["day"]: p for p in analytics.overview(db, 7, clock.now)["series"]}
    before = by_day[(today - timedelta(days=5)).isoformat()]
    launch = by_day[launch_day.isoformat()]
    assert before["followers"] == 1000  # без обрыва до подключения
    assert before["views"] == 5000  # старая кампания — первым известным значением
    assert launch["views_gain"] == 300  # новая кампания — с нуля, просмотры первого дня видны
    data = analytics.overview(db, 7, clock.now)
    assert data["deltas"]["views"] == 300 and data["deltas"]["followers"] == 0


def test_overview_api_refresh_and_export(analytics_on, admin_client, db, fake, tt, settings, clock, admin):
    from app.main import app

    queued = []
    app.dependency_overrides[get_analytics_enqueuer] = lambda: queued.extend
    account = connect_with_stats(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock, title='=HYPERLINK("http://evil")')
    drive(db, tt, settings, clock, rounds=3)
    analytics.collect_account(db, tt, account.id, clock.now)

    r = admin_client.get("/api/analytics/overview?days=30")
    assert r.status_code == 200, r.text
    overview = r.json()
    assert overview["enabled"] is True and overview["videos_tracked"] == 1
    assert overview["top_campaigns"][0]["id"] == campaign.id
    assert admin_client.get("/api/analytics/overview?days=12").status_code == 422

    assert admin_client.post("/api/analytics/refresh").json() == {"queued": 1}
    assert queued == [account.id]

    detail = admin_client.get(f"/api/campaigns/{campaign.id}").json()
    assert detail["stats"]["videos"] == 1 and detail["stats"]["views"] == detail["jobs"][0]["views"] > 0

    csv_text = admin_client.get("/api/analytics/export?kind=campaigns").text
    assert csv_text.startswith("﻿Кампания;")
    assert "'=HYPERLINK" in csv_text
    videos = admin_client.get("/api/analytics/export?kind=videos")
    assert videos.headers["content-disposition"].endswith('tiktok-videos.csv"')
    assert f"https://www.tiktok.com/@alpha/video/{jobs_of(db, campaign)[0].external_post_id}" in videos.text
    assert "alpha" in admin_client.get("/api/analytics/export?kind=accounts").text


def test_refresh_requires_analytics_enabled(admin_client):
    r = admin_client.post("/api/analytics/refresh")
    assert r.status_code == 400 and "TIKTOK_ANALYTICS" in r.json()["detail"]


def test_viewer_cannot_refresh(client, db, analytics_on):
    from app.models import Role, User
    from app.security import hash_password

    db.add(User(username="viewer", password_hash=hash_password("viewer-password"), role=Role.VIEWER))
    db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert client.get("/api/analytics/overview").status_code == 200
    assert client.post("/api/analytics/refresh").status_code == 403


def test_date_type_roundtrip(db):
    assert isinstance(date.today(), date)
