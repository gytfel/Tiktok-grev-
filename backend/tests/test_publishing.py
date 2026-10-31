import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import publishing
from app.accounts_service import check_account
from app.db import get_sessionmaker
from app.models import (
    AccountStatus,
    AccountToken,
    Campaign,
    CampaignStatus,
    JobStatus,
    PublicationAttempt,
    PublicationJob,
)
from app.publishing import CampaignError
from app.schemas import CampaignConfirm, PostSettings
from tests.conftest import confirm, connect, make_media

INIT = ("POST", "/v2/post/publish/video/init/")


def drive(db, tt, settings, clock, rounds=12, step=30):
    for _ in range(rounds):
        for job_id in publishing.dispatch_due_jobs(db, clock.now, 50):
            publishing.run_job(db, tt, settings, job_id, clock)
        for job_id in publishing.due_poll_job_ids(db, clock.now, settings.status_poll_interval_s):
            publishing.poll_job(db, tt, settings, job_id, clock)
        clock.advance(seconds=step)
    db.expire_all()


def jobs_of(db, campaign):
    return list(
        db.scalars(
            select(PublicationJob)
            .where(PublicationJob.campaign_id == campaign.id)
            .order_by(PublicationJob.id)
        )
    )


def test_publishes_one_video_to_each_selected_account(db, fake, tt, settings, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo", "charlie")]
    media = make_media(db)
    campaign = confirm(db, admin, media, accounts, clock)

    drive(db, tt, settings, clock)

    jobs = jobs_of(db, campaign)
    assert [j.status for j in jobs] == [JobStatus.PUBLISHED] * 3
    assert all(j.external_post_id for j in jobs)
    assert fake.calls.count(INIT) == 3
    assert {p.open_id for p in fake.publishes.values()} == {a.open_id for a in accounts}
    post_info = next(iter(fake.publishes.values())).post_info
    assert post_info["title"] == "Подпись #test"
    assert post_info["privacy_level"] == "PUBLIC_TO_EVERYONE"
    # Взаимодействия не включались пользователем — значит, выключены.
    assert post_info["disable_comment"] and post_info["disable_duet"] and post_info["disable_stitch"]
    assert db.get(Campaign, campaign.id).status == CampaignStatus.COMPLETED


def test_large_file_is_uploaded_in_chunks(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    size = 65_000_000 + 12345
    media = make_media(db, size=size)
    confirm(db, admin, media, [account], clock)

    drive(db, tt, settings, clock, rounds=3)

    pub = next(iter(fake.publishes.values()))
    assert pub.total_chunks == size // settings.upload_chunk_size
    assert len(pub.ranges) == pub.total_chunks
    assert pub.uploaded_bytes == size


def test_caption_override_and_creator_disabled_interactions(db, fake, tt, settings, clock, admin):
    fake.add_creator("quiet", comment_disabled=True)
    quiet = connect(db, fake, tt, "quiet", clock)
    alpha = connect(db, fake, tt, "alpha", clock)
    media = make_media(db)
    confirm(
        db,
        admin,
        media,
        [quiet, alpha],
        clock,
        caption_overrides={alpha.id: "Своя подпись"},
        settings=PostSettings(privacy_level="PUBLIC_TO_EVERYONE", allow_comment=True),
    )
    drive(db, tt, settings, clock, rounds=3)
    by_owner = {p.open_id: p.post_info for p in fake.publishes.values()}
    assert by_owner["open-quiet"]["disable_comment"] is True
    assert by_owner["open-alpha"]["disable_comment"] is False
    assert by_owner["open-alpha"]["title"] == "Своя подпись"
    assert by_owner["open-quiet"]["title"] == "Подпись #test"


def test_transient_init_error_is_retried_automatically(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/video/init/", (429, "rate_limit_exceeded"))

    drive(db, tt, settings, clock, rounds=1)
    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.QUEUED
    assert job.retryable and job.last_error_code == "rate_limit_exceeded"

    drive(db, tt, settings, clock, rounds=6)
    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.PUBLISHED
    assert len(job.attempts) == 2


def test_transient_errors_stop_after_max_attempts(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/video/init/", *[(500, "internal_error")] * 20)

    drive(db, tt, settings, clock, rounds=40, step=120)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.FAILED
    assert job.retryable
    assert len(job.attempts) == settings.max_auto_attempts


def test_permanent_error_is_not_retried(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/video/init/", (403, "spam_risk_too_many_posts"))

    drive(db, tt, settings, clock, rounds=5)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.FAILED
    assert job.retryable
    assert "дневной лимит" in job.last_error_message
    assert fake.calls.count(INIT) == 1
    assert db.get(Campaign, campaign.id).status == CampaignStatus.COMPLETED

    publishing.retry_job(db, job, admin, clock.now)
    db.commit()
    drive(db, tt, settings, clock, rounds=3)
    assert jobs_of(db, campaign)[0].status == JobStatus.PUBLISHED


def test_invalid_token_requires_reauth(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/creator_info/query/", (401, "access_token_invalid"))

    drive(db, tt, settings, clock, rounds=2)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.NEEDS_REAUTH
    assert db.get(type(account), account.id).connection_status == AccountStatus.NEEDS_REAUTH
    assert fake.calls.count(INIT) == 0


def test_expired_access_token_is_refreshed(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    token = db.get(AccountToken, account.id)
    old = token.access_token_enc
    token.access_expires_at = clock.now - timedelta(minutes=1)
    db.commit()
    fake.expire_access_tokens()

    drive(db, tt, settings, clock, rounds=3)

    assert jobs_of(db, campaign)[0].status == JobStatus.PUBLISHED
    assert db.get(AccountToken, account.id).access_token_enc != old


def test_revoked_refresh_token_requires_reauth(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    db.get(AccountToken, account.id).access_expires_at = clock.now
    db.commit()
    fake.refresh_tokens.clear()

    drive(db, tt, settings, clock, rounds=2)

    assert jobs_of(db, campaign)[0].status == JobStatus.NEEDS_REAUTH


def test_lost_response_on_final_chunk_does_not_duplicate(db, fake, tt, settings, clock, admin):
    """TikTok принял файл, но ответ потерялся: результат выясняется опросом, без повторного init."""
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.upload_faults[0] = ["drop_after", "drop_after", "drop_after", "drop_after"]

    drive(db, tt, settings, clock, rounds=4)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.PUBLISHED
    assert fake.calls.count(INIT) == 1
    assert len(fake.publishes) == 1


def test_incomplete_upload_is_retried_safely(db, fake, tt, settings, clock, admin):
    """Чанк отклонён и файл не загружен целиком — повтор безопасен и делается автоматически."""
    account = connect(db, fake, tt, "alpha", clock)
    media = make_media(db, size=65_000_000 + 1)
    campaign = confirm(db, admin, media, [account], clock)
    fake.upload_faults[1] = [400]

    drive(db, tt, settings, clock, rounds=1)
    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.PROCESSING
    assert not job.upload_complete

    drive(db, tt, settings, clock, rounds=12)
    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.PUBLISHED
    assert fake.calls.count(INIT) == 2
    first = db.scalar(
        select(PublicationAttempt).where(
            PublicationAttempt.job_id == job.id, PublicationAttempt.attempt_number == 1
        )
    )
    assert first.result_code == "upload_incomplete"
    completed = [p for p in fake.publishes.values() if p.completed_at is not None]
    assert len(completed) == 1


def test_tiktok_failure_marks_job_failed(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail_reason_for_next_publish = "spam_risk_text"

    drive(db, tt, settings, clock, rounds=4)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.FAILED
    assert job.retryable
    assert job.last_error_code == "spam_risk_text"


def test_stuck_processing_goes_to_manual_review(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.processing_delay_s = 10**9

    drive(db, tt, settings, clock, rounds=3)
    assert jobs_of(db, campaign)[0].status == JobStatus.PROCESSING
    clock.advance(seconds=settings.processing_timeout_s + 1)
    drive(db, tt, settings, clock, rounds=2)

    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.NEEDS_REVIEW
    assert not job.retryable
    with pytest.raises(CampaignError):
        publishing.retry_job(db, job, admin, clock.now)

    publishing.resolve_job(db, job, "published", admin, clock.now)
    db.commit()
    assert jobs_of(db, campaign)[0].status == JobStatus.PUBLISHED


def test_review_not_published_requeues(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/status/fetch/", *[(400, "invalid_publish_id")])
    drive(db, tt, settings, clock, rounds=2)
    job = jobs_of(db, campaign)[0]
    assert job.status == JobStatus.NEEDS_REVIEW

    publishing.resolve_job(db, job, "not_published", admin, clock.now)
    db.commit()
    drive(db, tt, settings, clock, rounds=3)
    assert jobs_of(db, campaign)[0].status == JobStatus.PUBLISHED


def test_one_active_publication_per_account(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    c1 = confirm(db, admin, make_media(db), [account], clock)
    c2 = confirm(db, admin, make_media(db), [account], clock)

    ids = publishing.dispatch_due_jobs(db, clock.now, 50)
    assert len(ids) == 2
    publishing.run_job(db, tt, settings, ids[0], clock)
    publishing.run_job(db, tt, settings, ids[1], clock)
    db.expire_all()
    statuses = sorted(j.status for j in jobs_of(db, c1) + jobs_of(db, c2))
    assert statuses == [JobStatus.PROCESSING, JobStatus.QUEUED]
    assert publishing.dispatch_due_jobs(db, clock.now, 50) == []

    drive(db, tt, settings, clock, rounds=4)
    assert all(j.status == JobStatus.PUBLISHED for j in jobs_of(db, c1) + jobs_of(db, c2))


def test_dispatch_respects_capacity(db, fake, tt, settings, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo", "charlie", "delta")]
    confirm(db, admin, make_media(db), accounts, clock)
    assert len(publishing.dispatch_due_jobs(db, clock.now, 3)) == 3
    assert publishing.dispatch_due_jobs(db, clock.now, 3) == []
    clock.advance(minutes=11)
    assert len(publishing.dispatch_due_jobs(db, clock.now, 3)) == 3


def test_scheduled_start_is_respected(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    confirm(db, admin, make_media(db), [account], clock, start_at=clock.now + timedelta(hours=1))
    assert publishing.dispatch_due_jobs(db, clock.now, 50) == []
    clock.advance(hours=1)
    assert len(publishing.dispatch_due_jobs(db, clock.now, 50)) == 1


def test_recover_stale_jobs(db, fake, tt, settings, clock, admin):
    a, b = (connect(db, fake, tt, n, clock) for n in ("alpha", "bravo"))
    campaign = confirm(db, admin, make_media(db), [a, b], clock)
    j1, j2 = jobs_of(db, campaign)
    for job in (j1, j2):
        publishing.claim_job(db, job.id, clock.now, 60)
    j2.publish_id = "v_pub_file~lost"
    db.commit()

    clock.advance(minutes=2)
    assert publishing.recover_stale_jobs(db, clock.now) == 2
    db.expire_all()
    assert db.get(PublicationJob, j1.id).status == JobStatus.QUEUED
    assert db.get(PublicationJob, j2.id).status == JobStatus.PROCESSING


def test_recover_completes_campaign_left_running(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    drive(db, tt, settings, clock, rounds=3)
    # Так выглядела гонка двух воркеров: все задания завершены, а статус остался прежним.
    db.get(Campaign, campaign.id).status = CampaignStatus.RUNNING
    db.commit()
    publishing.recover_stale_jobs(db, clock.now)
    db.expire_all()
    assert db.get(Campaign, campaign.id).status == CampaignStatus.COMPLETED


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="блокировки строк проверяются только на PostgreSQL",
)
def test_concurrent_job_completion_finishes_campaign(db, fake, tt, clock, admin):
    a, b = (connect(db, fake, tt, n, clock) for n in ("alpha", "bravo"))
    campaign = confirm(db, admin, make_media(db), [a, b], clock)
    j1, j2 = (j.id for j in jobs_of(db, campaign))
    sessions = get_sessionmaker()

    def finish(session, job_id):
        session.get(PublicationJob, job_id).status = JobStatus.PUBLISHED
        publishing.refresh_campaign_status(session, campaign.id)

    with sessions() as first:
        finish(first, j1)  # видит второе задание незавершённым и держит строку кампании
        second = threading.Thread(target=lambda: _finish_and_commit(sessions, finish, j2))
        second.start()
        threading.Event().wait(0.5)  # time.sleep в тестах подменён
        assert second.is_alive(), "второй пересчёт должен ждать коммита первого"
        first.commit()
    second.join(10)
    db.expire_all()
    assert db.get(Campaign, campaign.id).status == CampaignStatus.COMPLETED


def _finish_and_commit(sessions, finish, job_id):
    with sessions() as session:
        finish(session, job_id)
        session.commit()


def test_cancel_campaign_stops_queued_jobs(db, fake, tt, settings, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo")]
    campaign = confirm(db, admin, make_media(db), accounts, clock)
    assert publishing.cancel_campaign(db, campaign, admin) == 2
    db.commit()
    drive(db, tt, settings, clock, rounds=2)
    assert {j.status for j in jobs_of(db, campaign)} == {JobStatus.CANCELLED}
    assert fake.calls.count(INIT) == 0
    assert db.get(Campaign, campaign.id).status == CampaignStatus.CANCELLED


# --- предпросмотр и подтверждение --------------------------------------------------------


def _draft(media, accounts, **kw):
    data = {
        "media_asset_id": media.id,
        "title": "Кампания",
        "default_caption": "Текст",
        "account_ids": [a.id for a in accounts],
        "settings": PostSettings(privacy_level="PUBLIC_TO_EVERYONE"),
        "consent": True,
        "snapshot_hash": "",
    }
    data.update(kw)
    return CampaignConfirm(**data)


def test_preview_checks_each_account(db, fake, tt, clock, admin):
    fake.add_creator("friends", privacy_level_options=["MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"])
    fake.add_creator("shorty", max_video_post_duration_sec=15)
    ok, friends, shorty = (connect(db, fake, tt, n, clock) for n in ("alpha", "friends", "shorty"))
    for account in (ok, friends, shorty):
        assert check_account(db, account, tt, clock.now) is None
    disconnected = type(ok)(label="Без подключения")
    db.add(disconnected)
    db.commit()
    media = make_media(db, duration_ms=30_000)

    preview, _ = publishing.build_preview(db, _draft(media, [ok, friends, shorty, disconnected]))

    by_id = {c.account_id: c for c in preview.accounts}
    assert by_id[ok.id].ok
    assert "недоступна" in by_id[friends.id].problems[0]
    assert "длиннее" in by_id[shorty.id].problems[0]
    assert "не подключён" in by_id[disconnected.id].problems[0]
    assert preview.ready_count == 1 and preview.blocked_count == 3


def test_confirm_creates_jobs_only_for_ready_accounts(db, fake, tt, clock, admin):
    alpha = connect(db, fake, tt, "alpha", clock)
    pending = type(alpha)(label="Ожидает подключения")
    db.add(pending)
    db.commit()
    campaign = confirm(db, admin, make_media(db), [alpha, pending], clock)
    assert [j.account_id for j in jobs_of(db, campaign)] == [alpha.id]
    assert campaign.confirmed_by == admin.id


def test_confirm_rejects_stale_snapshot(db, fake, tt, clock, admin):
    alpha, bravo = (connect(db, fake, tt, n, clock) for n in ("alpha", "bravo"))
    media = make_media(db)
    data = _draft(media, [alpha, bravo])
    preview, _ = publishing.build_preview(db, data)
    data.snapshot_hash = preview.snapshot_hash
    # Пока пользователь смотрел на экран подтверждения, один аккаунт потерял авторизацию.
    bravo.connection_status = AccountStatus.NEEDS_REAUTH
    db.commit()

    with pytest.raises(CampaignError) as exc:
        publishing.confirm_campaign(db, data, admin, clock.now)
    assert exc.value.preview.ready_count == 1
    assert db.scalar(select(Campaign)) is None


def test_confirm_requires_consent(db, fake, tt, clock, admin):
    alpha = connect(db, fake, tt, "alpha", clock)
    with pytest.raises(CampaignError):
        confirm(db, admin, make_media(db), [alpha], clock, consent=False)


def test_branded_content_cannot_be_private(db, fake, tt, clock, admin):
    alpha = connect(db, fake, tt, "alpha", clock)
    data = _draft(
        make_media(db), [alpha], settings=PostSettings(privacy_level="SELF_ONLY", brand_content=True)
    )
    preview, _ = publishing.build_preview(db, data)
    assert preview.campaign_errors


def test_duplicates_are_blocked_unless_allowed(db, fake, tt, settings, clock, admin):
    alpha = connect(db, fake, tt, "alpha", clock)
    media = make_media(db)
    confirm(db, admin, media, [alpha], clock)
    drive(db, tt, settings, clock, rounds=3)

    preview, _ = publishing.build_preview(db, _draft(media, [alpha]))
    assert not preview.accounts[0].ok
    assert "уже опубликовано" in preview.accounts[0].problems[0]
    preview, _ = publishing.build_preview(db, _draft(media, [alpha], allow_duplicates=True))
    assert preview.accounts[0].ok and preview.accounts[0].warnings


def test_unaudited_app_limits(db, fake, tt, settings, clock, admin):
    """Без аудита TikTok: только «Только я» и не больше 5 публикующих аккаунтов за сутки."""
    fake.unaudited = True
    accounts = [
        connect(db, fake, tt, n, clock) for n in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    ]
    for account in accounts:
        check_account(db, account, tt, clock.now)
    media = make_media(db)
    preview, _ = publishing.build_preview(db, _draft(media, accounts))
    assert preview.ready_count == 0

    campaign = confirm(db, admin, media, accounts, clock, settings=PostSettings(privacy_level="SELF_ONLY"))
    drive(db, tt, settings, clock, rounds=4)
    statuses = [j.status for j in jobs_of(db, campaign)]
    assert statuses.count(JobStatus.PUBLISHED) == 5
    failed = [j for j in jobs_of(db, campaign) if j.status == JobStatus.FAILED]
    assert len(failed) == 1 and failed[0].last_error_code == "reached_active_user_cap"
    assert all(not j.external_post_id for j in jobs_of(db, campaign))
