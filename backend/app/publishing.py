"""Кампании и очередь публикаций через Direct Post.

Правило идемпотентности: задание повторяется только тогда, когда точно известно, что
публикация не состоялась:
  * ошибка произошла до того, как TikTok получил файл целиком (init, проверки, токен);
  * status/fetch вернул FAILED;
  * status/fetch подтверждает, что загружено меньше байт, чем размер файла.
Во всех неясных случаях задание уходит на ручную проверку (needs_review).
"""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.accounts_service import NeedsReauth, apply_creator_info, ensure_access_token, mark_needs_reauth
from app.config import Settings
from app.media import media_path
from app.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignStatus,
    JobStatus,
    MediaAsset,
    PublicationAttempt,
    PublicationJob,
    User,
    utcnow,
)
from app.schemas import AccountCheck, CampaignConfirm, CampaignDraft, CampaignPreview, PostSettings
from app.tiktok import TikTokClient, TikTokError, chunk_ranges, describe, plan_chunks
from app.tiktok.client import CONTENT_TYPES

CAPTION_LIMIT = 2200
REDISPATCH_AFTER = timedelta(minutes=10)
AUTO_RETRY_FAIL_REASONS = {"internal"}
PRIVACY_LABELS = {
    "PUBLIC_TO_EVERYONE": "Все",
    "MUTUAL_FOLLOW_FRIENDS": "Друзья",
    "FOLLOWER_OF_CREATOR": "Подписчики",
    "SELF_ONLY": "Только я",
}


class CampaignError(Exception):
    def __init__(self, message: str, preview: CampaignPreview | None = None):
        self.message = message
        self.preview = preview
        super().__init__(message)


def caption_length(text: str) -> int:
    # TikTok считает длину подписи в UTF-16 code units.
    return len(text.encode("utf-16-le")) // 2


# --- предпросмотр и подтверждение --------------------------------------------------------


def _duplicate_account_ids(db: Session, media: MediaAsset, account_ids: list[int]) -> set[int]:
    rows = db.execute(
        select(PublicationJob.account_id)
        .join(Campaign, Campaign.id == PublicationJob.campaign_id)
        .join(MediaAsset, MediaAsset.id == Campaign.media_asset_id)
        .where(
            MediaAsset.sha256 == media.sha256,
            PublicationJob.account_id.in_(account_ids),
            PublicationJob.status.in_((JobStatus.PUBLISHED, JobStatus.NEEDS_REVIEW, *JobStatus.PENDING)),
        )
    )
    return {r[0] for r in rows}


def _check_creator(
    settings: PostSettings, info: dict[str, Any] | None, media: MediaAsset
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    if not info:
        warnings.append("Настройки аккаунта ещё не проверялись — проверка пройдёт перед публикацией")
        return problems, warnings
    options = info.get("privacy_level_options") or []
    if settings.privacy_level not in options:
        allowed = ", ".join(PRIVACY_LABELS.get(o, o) for o in options) or "нет"
        problems.append(
            f"Видимость «{PRIVACY_LABELS[settings.privacy_level]}» недоступна (доступно: {allowed})"
        )
    max_sec = info.get("max_video_post_duration_sec")
    if max_sec and media.duration_ms and media.duration_ms > max_sec * 1000:
        problems.append(f"Видео длиннее разрешённого для аккаунта ({max_sec} с)")
    for flag, allowed_attr, name in (
        ("comment_disabled", "allow_comment", "Комментарии"),
        ("duet_disabled", "allow_duet", "Duet"),
        ("stitch_disabled", "allow_stitch", "Stitch"),
    ):
        if info.get(flag) and getattr(settings, allowed_attr):
            warnings.append(f"{name} отключены в настройках аккаунта и будут выключены")
    return problems, warnings


def build_preview(db: Session, draft: CampaignDraft) -> tuple[CampaignPreview, MediaAsset | None]:
    errors: list[str] = []
    media = db.get(MediaAsset, draft.media_asset_id)
    if media is None:
        errors.append("Видео не найдено")
    elif media.validation_status != "valid":
        errors.append("Видео не прошло проверку требований TikTok")
    s = draft.settings
    if s.brand_content and s.privacy_level == "SELF_ONLY":
        errors.append("Брендированный контент нельзя публиковать с видимостью «Только я»")

    ids = list(dict.fromkeys(draft.account_ids))
    accounts = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(ids))).scalars()}
    duplicates = _duplicate_account_ids(db, media, ids) if media else set()

    checks: list[AccountCheck] = []
    for account_id in ids:
        account = accounts.get(account_id)
        if account is None:
            checks.append(
                AccountCheck(account_id=account_id, label="—", ok=False, problems=["Аккаунт не найден"])
            )
            continue
        override = draft.caption_overrides.get(account_id)
        caption = override if override is not None else draft.default_caption
        problems: list[str] = []
        warnings: list[str] = []
        if account.connection_status == AccountStatus.NOT_CONNECTED:
            problems.append("Аккаунт не подключён к TikTok")
        elif account.connection_status == AccountStatus.NEEDS_REAUTH:
            problems.append("Нужна повторная авторизация")
        if media is not None:
            p, w = _check_creator(s, account.creator_info, media)
            problems += p
            warnings += w
        if caption_length(caption) > CAPTION_LIMIT:
            problems.append(f"Подпись длиннее {CAPTION_LIMIT} символов")
        if account_id in duplicates:
            message = "Это видео уже опубликовано или стоит в очереди на этом аккаунте"
            (warnings if draft.allow_duplicates else problems).append(message)
        checks.append(
            AccountCheck(
                account_id=account.id,
                label=account.label,
                display_name=account.display_name,
                username=account.username,
                avatar_url=account.avatar_url,
                ok=not problems,
                problems=problems,
                warnings=warnings,
                caption=caption,
                caption_overridden=override is not None,
                creator_info_checked_at=account.creator_info_checked_at,
            )
        )

    ready = sorted((c.account_id, c.caption) for c in checks if c.ok)
    snapshot = {
        "media_id": draft.media_asset_id,
        "media_sha256": media.sha256 if media else None,
        "title": draft.title,
        "settings": s.model_dump(),
        "start_at": draft.start_at.isoformat() if draft.start_at else None,
        "ready": ready,
        "errors": errors,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    preview = CampaignPreview(
        snapshot_hash=digest,
        campaign_errors=errors,
        ready_count=len(ready),
        blocked_count=len(checks) - len(ready),
        accounts=checks,
    )
    return preview, media


def confirm_campaign(db: Session, data: CampaignConfirm, user: User, now: datetime) -> Campaign:
    if not data.consent:
        raise CampaignError("Нужно подтвердить согласие с правилами TikTok перед публикацией")
    preview, media = build_preview(db, data)
    if preview.snapshot_hash != data.snapshot_hash:
        raise CampaignError(
            "Данные изменились после проверки. Посмотрите обновлённый список и подтвердите снова.",
            preview,
        )
    if preview.campaign_errors:
        raise CampaignError(preview.campaign_errors[0], preview)
    if preview.ready_count == 0:
        raise CampaignError("Нет аккаунтов, готовых к публикации", preview)

    start_at = max(data.start_at or now, now)
    settings = data.settings.model_dump()
    campaign = Campaign(
        media_asset_id=media.id,
        title=data.title,
        default_caption=data.default_caption,
        settings={**settings, "allow_duplicates": data.allow_duplicates},
        status=CampaignStatus.RUNNING,
        snapshot_hash=preview.snapshot_hash,
        start_at=start_at,
        confirmed_by=user.id,
        confirmed_at=now,
    )
    db.add(campaign)
    for check in preview.accounts:
        if check.ok:
            campaign.jobs.append(
                PublicationJob(
                    account_id=check.account_id,
                    status=JobStatus.QUEUED,
                    caption=check.caption,
                    caption_overridden=check.caption_overridden,
                    settings=dict(settings),
                    scheduled_at=start_at,
                )
            )
    db.flush()
    audit.record(
        db,
        user,
        "campaign.confirmed",
        "campaign",
        campaign.id,
        {
            "title": campaign.title,
            "media_sha256": media.sha256,
            "jobs": preview.ready_count,
            "excluded": preview.blocked_count,
            "privacy_level": settings["privacy_level"],
            "snapshot_hash": preview.snapshot_hash,
        },
    )
    db.commit()
    return campaign


def refresh_campaign_status(db: Session, campaign_id: int) -> None:
    campaign = db.get(Campaign, campaign_id)
    if campaign.status == CampaignStatus.CANCELLED:
        return
    pending = db.scalar(
        select(func.count())
        .select_from(PublicationJob)
        .where(PublicationJob.campaign_id == campaign_id, PublicationJob.status.in_(JobStatus.PENDING))
    )
    campaign.status = CampaignStatus.RUNNING if pending else CampaignStatus.COMPLETED


def cancel_campaign(db: Session, campaign: Campaign, user: User) -> int:
    """Отменяет задания, которые ещё ждут в очереди. Начатые публикации доводятся до конца."""
    cancelled = 0
    for job in campaign.jobs:
        if job.status == JobStatus.QUEUED:
            cancel_job(db, job, None)
            cancelled += 1
    campaign.status = CampaignStatus.CANCELLED
    audit.record(db, user, "campaign.cancelled", "campaign", campaign.id, {"cancelled_jobs": cancelled})
    return cancelled


# --- диспетчеризация ---------------------------------------------------------------------


def dispatch_due_jobs(db: Session, now: datetime, limit: int) -> list[int]:
    """Выбирает задания, которые пора отправить воркеру.

    Ограничивает число уже отправленных, но ещё не взятых заданий, и не берёт аккаунты,
    у которых уже идёт публикация.
    """
    in_flight = db.scalar(
        select(func.count())
        .select_from(PublicationJob)
        .where(
            PublicationJob.status == JobStatus.QUEUED,
            PublicationJob.dispatched_at >= now - REDISPATCH_AFTER,
        )
    )
    capacity = limit - (in_flight or 0)
    if capacity <= 0:
        return []
    busy_accounts = select(PublicationJob.account_id).where(PublicationJob.status.in_(JobStatus.ACTIVE))
    ids = list(
        db.scalars(
            select(PublicationJob.id)
            .where(
                PublicationJob.status == JobStatus.QUEUED,
                PublicationJob.scheduled_at <= now,
                (PublicationJob.dispatched_at.is_(None))
                | (PublicationJob.dispatched_at < now - REDISPATCH_AFTER),
                PublicationJob.account_id.not_in(busy_accounts),
            )
            .order_by(PublicationJob.scheduled_at, PublicationJob.id)
            .limit(capacity)
        )
    )
    if ids:
        db.execute(update(PublicationJob).where(PublicationJob.id.in_(ids)).values(dispatched_at=now))
    db.commit()
    return ids


def due_poll_job_ids(db: Session, now: datetime, interval_s: int, limit: int = 500) -> list[int]:
    ids = list(
        db.scalars(
            select(PublicationJob.id)
            .where(
                PublicationJob.status == JobStatus.PROCESSING,
                PublicationJob.next_poll_at <= now,
            )
            .order_by(PublicationJob.next_poll_at)
            .limit(limit)
        )
    )
    if ids:
        # Сдвигаем время следующего опроса, чтобы задание не попало в очередь дважды.
        db.execute(
            update(PublicationJob)
            .where(PublicationJob.id.in_(ids))
            .values(next_poll_at=now + timedelta(seconds=interval_s * 3))
        )
    db.commit()
    return ids


def recover_stale_jobs(db: Session, now: datetime) -> int:
    """Возвращает в работу задания, воркер которых пропал посреди загрузки."""
    jobs = db.scalars(
        select(PublicationJob).where(
            PublicationJob.status == JobStatus.UPLOADING, PublicationJob.lease_until < now
        )
    ).all()
    for job in jobs:
        attempt = _current_attempt(db, job)
        if job.publish_id:
            # TikTok мог получить файл целиком — результат выясняем через status/fetch.
            job.status = JobStatus.PROCESSING
            job.processing_since = job.processing_since or now
            job.next_poll_at = now
            job.lease_until = None
        else:
            _finish_attempt(attempt, now, "worker_lost", "Воркер остановился до начала загрузки")
            job.status = JobStatus.QUEUED
            job.scheduled_at = now
            job.dispatched_at = None
            job.lease_until = None
    db.commit()
    return len(jobs)


# --- выполнение задания ------------------------------------------------------------------


def _current_attempt(db: Session, job: PublicationJob) -> PublicationAttempt | None:
    return db.scalar(
        select(PublicationAttempt)
        .where(PublicationAttempt.job_id == job.id)
        .order_by(PublicationAttempt.attempt_number.desc())
        .limit(1)
    )


def _finish_attempt(
    attempt: PublicationAttempt | None, now: datetime, code: str, details: str | None = None
) -> None:
    if attempt is not None and attempt.finished_at is None:
        attempt.finished_at = now
        attempt.result_code = code
        if details:
            attempt.error_details = (
                f"{attempt.error_details}\n{details}" if attempt.error_details else details
            )


def _set_error(job: PublicationJob, code: str, message: str) -> None:
    job.last_error_code = code
    job.last_error_message = message


def _retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempt - 1, 0), 1800))


def _not_published(
    db: Session,
    job: PublicationJob,
    now: datetime,
    settings: Settings,
    code: str,
    message: str,
    *,
    auto_retry: bool,
) -> None:
    """Публикация точно не состоялась: повторяем автоматически или ждём ручного повтора."""
    _set_error(job, code, message)
    job.retryable = True
    job.lease_until = None
    job.next_poll_at = None
    if (
        auto_retry
        and job.auto_attempts < settings.max_auto_attempts
        and job.campaign.status != CampaignStatus.CANCELLED
    ):
        job.status = JobStatus.QUEUED
        job.scheduled_at = now + _retry_delay(job.auto_attempts)
        job.dispatched_at = None
    else:
        job.status = JobStatus.FAILED
    refresh_campaign_status(db, job.campaign_id)


def _needs_reauth(db: Session, job: PublicationJob, code: str, message: str) -> None:
    _set_error(job, code, message)
    job.status = JobStatus.NEEDS_REAUTH
    job.retryable = True
    job.lease_until = None
    refresh_campaign_status(db, job.campaign_id)


def _needs_review(db: Session, job: PublicationJob, code: str, message: str) -> None:
    _set_error(job, code, message)
    job.status = JobStatus.NEEDS_REVIEW
    job.retryable = False
    job.lease_until = None
    job.next_poll_at = None
    refresh_campaign_status(db, job.campaign_id)


def claim_job(db: Session, job_id: int, now: datetime, lease_s: int) -> PublicationJob | None:
    job = db.execute(
        select(PublicationJob).where(PublicationJob.id == job_id).with_for_update(of=PublicationJob)
    ).scalar_one_or_none()
    if job is None or job.status != JobStatus.QUEUED or job.scheduled_at > now:
        db.rollback()
        return None
    # Блокировка строки аккаунта: на одном аккаунте одновременно идёт не больше одной публикации.
    db.execute(select(Account.id).where(Account.id == job.account_id).with_for_update())
    busy = db.scalar(
        select(func.count())
        .select_from(PublicationJob)
        .where(
            PublicationJob.account_id == job.account_id,
            PublicationJob.status.in_(JobStatus.ACTIVE),
            PublicationJob.id != job.id,
        )
    )
    if busy:
        job.dispatched_at = None
        db.commit()
        return None
    attempt_number = (
        db.scalar(
            select(func.max(PublicationAttempt.attempt_number)).where(PublicationAttempt.job_id == job.id)
        )
        or 0
    ) + 1
    job.status = JobStatus.UPLOADING
    job.lease_until = now + timedelta(seconds=lease_s)
    job.auto_attempts += 1
    job.publish_id = None
    job.upload_complete = False
    job.processing_since = None
    job.incomplete_since = None
    db.add(PublicationAttempt(job_id=job.id, attempt_number=attempt_number, started_at=now))
    db.commit()
    return job


def build_post_info(job: PublicationJob, creator: dict[str, Any]) -> dict[str, Any]:
    s = job.settings
    info: dict[str, Any] = {
        "title": job.caption,
        "privacy_level": s["privacy_level"],
        "disable_comment": not s["allow_comment"] or bool(creator.get("comment_disabled")),
        "disable_duet": not s["allow_duet"] or bool(creator.get("duet_disabled")),
        "disable_stitch": not s["allow_stitch"] or bool(creator.get("stitch_disabled")),
        "brand_content_toggle": bool(s["brand_content"]),
        "brand_organic_toggle": bool(s["brand_organic"]),
        "is_aigc": bool(s.get("is_aigc")),
    }
    if s.get("cover_timestamp_ms") is not None:
        info["video_cover_timestamp_ms"] = s["cover_timestamp_ms"]
    return info


def upload_file(
    tt: TikTokClient,
    upload_url: str,
    path: Path,
    video_size: int,
    chunk_size: int,
    total_chunks: int,
    content_type: str,
    on_chunk: Callable[[int], None],
    chunk_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Загружает файл чанками. Повторная отправка того же диапазона байт безопасна."""
    with open(path, "rb") as f:
        for start, end in chunk_ranges(video_size, chunk_size, total_chunks):
            f.seek(start)
            data = f.read(end - start + 1)
            for attempt in range(chunk_retries + 1):
                try:
                    tt.upload_chunk(upload_url, data, start, end, video_size, content_type)
                    break
                except TikTokError as exc:
                    if not exc.transient or attempt == chunk_retries:
                        raise
                    sleep(2 ** (attempt + 1))
            on_chunk(end + 1)


def run_job(
    db: Session,
    tt: TikTokClient,
    settings: Settings,
    job_id: int,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    now = clock()
    job = claim_job(db, job_id, now, settings.job_lease_s)
    if job is None:
        return
    attempt = _current_attempt(db, job)
    account = job.account
    media = job.campaign.media
    path = media_path(settings.media_dir, media.storage_key)

    # Этап 1: всё, что происходит до создания публикации в TikTok. Ошибки здесь означают,
    # что видео точно не опубликовано.
    try:
        if not path.exists():
            _finish_attempt(attempt, now, "media_missing", str(path))
            _not_published(
                db,
                job,
                now,
                settings,
                "media_missing",
                "Файл видео не найден в хранилище",
                auto_retry=False,
            )
            db.commit()
            return
        token = ensure_access_token(db, account, tt, now)
        creator = tt.creator_info(token)
        apply_creator_info(account, creator, now)
        if job.settings["privacy_level"] not in (creator.get("privacy_level_options") or []):
            message = "Выбранная видимость недоступна для аккаунта"
            _finish_attempt(attempt, now, "privacy_level_option_mismatch", message)
            _not_published(db, job, now, settings, "privacy_level_option_mismatch", message, auto_retry=False)
            db.commit()
            return
        max_sec = creator.get("max_video_post_duration_sec")
        if max_sec and media.duration_ms and media.duration_ms > max_sec * 1000:
            message = f"Видео длиннее разрешённого для аккаунта ({max_sec} с)"
            _finish_attempt(attempt, now, "duration_too_long", message)
            _not_published(db, job, now, settings, "duration_too_long", message, auto_retry=False)
            db.commit()
            return
        chunk_size, total_chunks = plan_chunks(media.size_bytes, settings.upload_chunk_size)
        publish_id, upload_url = tt.init_direct_post(
            token, build_post_info(job, creator), media.size_bytes, chunk_size, total_chunks
        )
    except NeedsReauth as exc:
        now = clock()
        _finish_attempt(attempt, now, exc.code, exc.message)
        _needs_reauth(db, job, exc.code, exc.message)
        db.commit()
        return
    except TikTokError as exc:
        now = clock()
        message = describe(exc.code, exc.message)
        _finish_attempt(attempt, now, exc.code, exc.details())
        if exc.is_auth_problem:
            mark_needs_reauth(account, message)
            _needs_reauth(db, job, exc.code, message)
        else:
            _not_published(db, job, now, settings, exc.code, message, auto_retry=exc.transient)
        db.commit()
        return

    # Этап 2: publish_id сохраняется до загрузки — с этого момента результат выясняется
    # только через status/fetch.
    job.publish_id = publish_id
    attempt.publish_id = publish_id
    db.commit()

    def on_chunk(uploaded: int) -> None:
        attempt.uploaded_bytes = uploaded
        job.lease_until = clock() + timedelta(seconds=settings.job_lease_s)
        db.commit()

    try:
        upload_file(
            tt,
            upload_url,
            path,
            media.size_bytes,
            chunk_size,
            total_chunks,
            CONTENT_TYPES.get(Path(media.storage_key).suffix.lstrip("."), "video/mp4"),
            on_chunk,
        )
        job.upload_complete = True
    except (TikTokError, OSError) as exc:
        detail = exc.details() if isinstance(exc, TikTokError) else str(exc)
        attempt.error_details = f"upload: {detail}"

    now = clock()
    job.status = JobStatus.PROCESSING
    job.processing_since = now
    job.next_poll_at = now + timedelta(seconds=3)
    job.lease_until = None
    db.commit()


def poll_job(
    db: Session,
    tt: TikTokClient,
    settings: Settings,
    job_id: int,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    now = clock()
    job = db.execute(
        select(PublicationJob).where(PublicationJob.id == job_id).with_for_update(of=PublicationJob)
    ).scalar_one_or_none()
    if job is None or job.status != JobStatus.PROCESSING:
        db.rollback()
        return
    publish_id = job.publish_id
    timed_out = now - (job.processing_since or now) > timedelta(seconds=settings.processing_timeout_s)
    # Блокировку не держим на время запроса к TikTok.
    db.commit()

    try:
        token = ensure_access_token(db, job.account, tt, now)
        status = tt.fetch_status(token, publish_id)
    except NeedsReauth as exc:
        job = _relock_processing(db, job_id, publish_id)
        if job is None:
            return
        attempt = _current_attempt(db, job)
        _finish_attempt(attempt, now, "status_unknown", exc.message)
        _needs_review(
            db,
            job,
            "status_unknown",
            f"Не удалось проверить результат: {exc.message}. Проверьте профиль вручную.",
        )
        db.commit()
        return
    except TikTokError as exc:
        db.rollback()
        job = _relock_processing(db, job_id, publish_id)
        if job is None:
            return
        attempt = _current_attempt(db, job)
        if exc.is_auth_problem:
            mark_needs_reauth(job.account, describe(exc.code, exc.message))
        if exc.transient and not timed_out and not exc.is_auth_problem:
            job.next_poll_at = now + timedelta(seconds=settings.status_poll_interval_s * 3)
            db.commit()
            return
        _finish_attempt(attempt, now, "status_unknown", exc.details())
        _needs_review(
            db,
            job,
            "status_unknown",
            f"Не удалось проверить результат ({describe(exc.code, exc.code)}). Проверьте профиль вручную.",
        )
        db.commit()
        return

    job = _relock_processing(db, job_id, publish_id)
    if job is None:
        return
    attempt = _current_attempt(db, job)
    state = status.get("status")
    if state == "PUBLISH_COMPLETE":
        post_ids = status.get("publicaly_available_post_id") or status.get("publicly_available_post_id") or []
        job.status = JobStatus.PUBLISHED
        job.published_at = now
        job.external_post_id = str(post_ids[0]) if post_ids else None
        job.last_error_code = None
        job.last_error_message = None
        job.retryable = False
        job.next_poll_at = None
        _finish_attempt(attempt, now, "published")
        refresh_campaign_status(db, job.campaign_id)
    elif state == "FAILED":
        reason = status.get("fail_reason") or "unknown"
        _finish_attempt(attempt, now, reason)
        _not_published(
            db,
            job,
            now,
            settings,
            reason,
            describe(reason, f"TikTok: {reason}"),
            auto_retry=reason in AUTO_RETRY_FAIL_REASONS,
        )
    elif state == "SEND_TO_USER_INBOX":
        _finish_attempt(attempt, now, "sent_to_inbox")
        _needs_review(
            db,
            job,
            "sent_to_inbox",
            "Видео попало во входящие TikTok — завершите публикацию в приложении",
        )
    elif state == "PROCESSING_UPLOAD" and not job.upload_complete:
        uploaded = status.get("uploaded_bytes")
        size = job.campaign.media.size_bytes
        if isinstance(uploaded, int) and uploaded >= size:
            job.upload_complete = True
            job.incomplete_since = None
        elif isinstance(uploaded, int) and uploaded < size:
            if job.incomplete_since is None:
                job.incomplete_since = now
            elif now - job.incomplete_since >= timedelta(seconds=settings.incomplete_upload_grace_s):
                # Файл не дошёл целиком и уже не дойдёт: дальше мы его не отправляем.
                _finish_attempt(attempt, now, "upload_incomplete", f"uploaded_bytes={uploaded}/{size}")
                _not_published(
                    db,
                    job,
                    now,
                    settings,
                    "upload_incomplete",
                    describe("upload_incomplete"),
                    auto_retry=True,
                )
                db.commit()
                return
        _schedule_next_poll(db, job, now, settings, timed_out, attempt)
    else:
        _schedule_next_poll(db, job, now, settings, timed_out, attempt)
    db.commit()


def _relock_processing(db: Session, job_id: int, publish_id: str | None) -> PublicationJob | None:
    """Повторно берёт задание под блокировку и проверяет, что его не изменили параллельно."""
    job = db.execute(
        select(PublicationJob)
        .where(PublicationJob.id == job_id)
        .with_for_update(of=PublicationJob)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if job is None or job.status != JobStatus.PROCESSING or job.publish_id != publish_id:
        db.rollback()
        return None
    return job


def _schedule_next_poll(
    db: Session,
    job: PublicationJob,
    now: datetime,
    settings: Settings,
    timed_out: bool,
    attempt: PublicationAttempt | None,
) -> None:
    if timed_out:
        _finish_attempt(attempt, now, "processing_timeout")
        _needs_review(
            db,
            job,
            "processing_timeout",
            "TikTok долго не подтверждает результат. Проверьте профиль вручную.",
        )
    else:
        job.next_poll_at = now + timedelta(seconds=settings.status_poll_interval_s)


# --- ручные действия ---------------------------------------------------------------------


def retry_job(db: Session, job: PublicationJob, user: User, now: datetime) -> None:
    if job.status == JobStatus.FAILED and job.retryable:
        pass
    elif job.status == JobStatus.NEEDS_REAUTH:
        if job.account.connection_status != AccountStatus.CONNECTED:
            raise CampaignError("Сначала подключите аккаунт заново")
    else:
        raise CampaignError("Повтор доступен только для заданий, которые точно не опубликованы")
    _requeue(job, now)
    audit.record(db, user, "job.retried", "job", job.id, {"campaign_id": job.campaign_id})
    refresh_campaign_status(db, job.campaign_id)


def resolve_job(db: Session, job: PublicationJob, outcome: str, user: User, now: datetime) -> None:
    if job.status != JobStatus.NEEDS_REVIEW:
        raise CampaignError("Задание не ожидает ручной проверки")
    if outcome == "published":
        job.status = JobStatus.PUBLISHED
        job.published_at = now
        job.retryable = False
    else:
        _requeue(job, now)
    audit.record(
        db, user, "job.resolved", "job", job.id, {"outcome": outcome, "campaign_id": job.campaign_id}
    )
    refresh_campaign_status(db, job.campaign_id)


def cancel_job(db: Session, job: PublicationJob, user: User | None) -> None:
    if job.status != JobStatus.QUEUED:
        raise CampaignError("Отменить можно только задание, которое ещё ждёт в очереди")
    job.status = JobStatus.CANCELLED
    job.retryable = False
    if user is not None:
        audit.record(db, user, "job.cancelled", "job", job.id, {"campaign_id": job.campaign_id})
        refresh_campaign_status(db, job.campaign_id)


def _requeue(job: PublicationJob, now: datetime) -> None:
    job.campaign.status = CampaignStatus.RUNNING
    job.campaign.notified_at = None
    job.attention_notified_at = None
    job.status = JobStatus.QUEUED
    job.scheduled_at = now
    job.dispatched_at = None
    job.auto_attempts = 0
    job.publish_id = None
    job.upload_complete = False
    job.processing_since = None
    job.incomplete_since = None
    job.next_poll_at = None
    job.retryable = False
    job.last_error_code = None
    job.last_error_message = None
