from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import current_user, require_writer
from app.models import (
    AccountStatus,
    AuditEvent,
    Campaign,
    JobStatus,
    PublicationAttempt,
    PublicationJob,
    User,
    utcnow,
)
from app.publishing import (
    CampaignError,
    build_preview,
    cancel_campaign,
    cancel_job,
    confirm_campaign,
    resolve_job,
    retry_job,
)
from app.routers.media import _to_out
from app.schemas import (
    AttemptOut,
    AuditOut,
    CampaignConfirm,
    CampaignDetail,
    CampaignDraft,
    CampaignPreview,
    CampaignSummary,
    JobOut,
    ResolveIn,
)

router = APIRouter(prefix="/api", tags=["campaigns"])


def _conflict(exc: CampaignError) -> JSONResponse:
    body = {"detail": exc.message}
    if exc.preview is not None:
        body["preview"] = exc.preview.model_dump(mode="json")
    return JSONResponse(body, status_code=409)


def _counts(db: Session, campaign_ids: list[int]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {cid: {} for cid in campaign_ids}
    rows = db.execute(
        select(PublicationJob.campaign_id, PublicationJob.status, func.count())
        .where(PublicationJob.campaign_id.in_(campaign_ids))
        .group_by(PublicationJob.campaign_id, PublicationJob.status)
    )
    for campaign_id, status, count in rows:
        result[campaign_id][status] = count
    return result


def _summary(campaign: Campaign, counts: dict[str, int]) -> dict:
    return {
        "id": campaign.id,
        "title": campaign.title,
        "status": campaign.status,
        "media": _to_out(campaign.media, 0),
        "default_caption": campaign.default_caption,
        "settings": campaign.settings,
        "start_at": campaign.start_at,
        "confirmed_at": campaign.confirmed_at,
        "confirmed_by": campaign.confirmer.username if campaign.confirmer else None,
        "counts": counts,
        "total": sum(counts.values()),
    }


def _get_campaign(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Кампания не найдена")
    return campaign


def _get_job(db: Session, job_id: int) -> PublicationJob:
    job = db.get(PublicationJob, job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return job


@router.get("/campaigns", response_model=list[CampaignSummary])
def list_campaigns(db: Session = Depends(get_db), _: User = Depends(current_user)):
    campaigns = list(db.scalars(select(Campaign).order_by(Campaign.id.desc()).limit(200)))
    counts = _counts(db, [c.id for c in campaigns])
    return [_summary(c, counts[c.id]) for c in campaigns]


@router.post("/campaigns/preview", response_model=CampaignPreview)
def preview_campaign(draft: CampaignDraft, db: Session = Depends(get_db), _: User = Depends(require_writer)):
    preview, _media = build_preview(db, draft)
    return preview


@router.post("/campaigns", response_model=CampaignSummary)
def create_campaign(
    data: CampaignConfirm, db: Session = Depends(get_db), user: User = Depends(require_writer)
):
    try:
        campaign = confirm_campaign(db, data, user, utcnow())
    except CampaignError as exc:
        db.rollback()
        return _conflict(exc)
    return _summary(campaign, _counts(db, [campaign.id])[campaign.id])


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    campaign = _get_campaign(db, campaign_id)
    attempt_totals = dict(
        db.execute(
            select(PublicationAttempt.job_id, func.count())
            .join(PublicationJob, PublicationJob.id == PublicationAttempt.job_id)
            .where(PublicationJob.campaign_id == campaign.id)
            .group_by(PublicationAttempt.job_id)
        ).all()
    )
    jobs = db.scalars(
        select(PublicationJob).where(PublicationJob.campaign_id == campaign.id).order_by(PublicationJob.id)
    )
    job_out = [
        JobOut(
            id=j.id,
            account_id=j.account_id,
            account_label=j.account.label,
            account_display_name=j.account.display_name,
            account_username=j.account.username,
            status=j.status,
            caption=j.caption,
            caption_overridden=j.caption_overridden,
            scheduled_at=j.scheduled_at,
            auto_attempts=j.auto_attempts,
            attempt_total=attempt_totals.get(j.id, 0),
            publish_id=j.publish_id,
            published_at=j.published_at,
            external_post_id=j.external_post_id,
            last_error_code=j.last_error_code,
            last_error_message=j.last_error_message,
            retryable=j.retryable,
            updated_at=j.updated_at,
            views=j.views,
            likes=j.likes,
            comments=j.comments,
            shares=j.shares,
        )
        for j in jobs
    ]
    with_stats = [j for j in job_out if j.views is not None]
    stats = (
        {
            "videos": len(with_stats),
            **{
                m: sum(getattr(j, m) or 0 for j in with_stats)
                for m in ("views", "likes", "comments", "shares")
            },
        }
        if with_stats
        else None
    )
    return {**_summary(campaign, _counts(db, [campaign.id])[campaign.id]), "jobs": job_out, "stats": stats}


@router.post("/campaigns/{campaign_id}/cancel")
def cancel(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    campaign = _get_campaign(db, campaign_id)
    cancelled = cancel_campaign(db, campaign, user)
    db.commit()
    return {"ok": True, "cancelled": cancelled}


@router.delete("/campaigns/{campaign_id}")
def delete(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    """Удаляет кампанию из панели вместе с заданиями и статистикой. Видео в TikTok остаются."""
    campaign = _get_campaign(db, campaign_id)
    # Блокировка заданий: воркер не возьмёт задание в работу, пока идёт удаление.
    jobs = db.scalars(
        select(PublicationJob)
        .where(PublicationJob.campaign_id == campaign.id)
        .with_for_update(of=PublicationJob)
    ).all()
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    if any(job.status in JobStatus.ACTIVE for job in jobs):
        raise HTTPException(409, "Идёт загрузка в TikTok — удалить кампанию можно после её завершения")
    audit.record(
        db, user, "campaign.deleted", "campaign", campaign.id, {"title": campaign.title, "jobs": counts}
    )
    db.delete(campaign)
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/retry-failed")
def retry_failed(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    campaign = _get_campaign(db, campaign_id)
    now = utcnow()
    retried = 0
    for job in campaign.jobs:
        eligible = (job.status == JobStatus.FAILED and job.retryable) or (
            job.status == JobStatus.NEEDS_REAUTH and job.account.connection_status == AccountStatus.CONNECTED
        )
        if eligible:
            retry_job(db, job, user, now)
            retried += 1
    db.commit()
    return {"ok": True, "retried": retried}


@router.get("/jobs/{job_id}/attempts", response_model=list[AttemptOut])
def job_attempts(job_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _get_job(db, job_id).attempts


@router.post("/jobs/{job_id}/retry")
def retry(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    job = _get_job(db, job_id)
    try:
        retry_job(db, job, user, utcnow())
    except CampaignError as exc:
        raise HTTPException(409, exc.message) from None
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
def cancel_one(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    job = _get_job(db, job_id)
    try:
        cancel_job(db, job, user)
    except CampaignError as exc:
        raise HTTPException(409, exc.message) from None
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/resolve")
def resolve(
    job_id: int, data: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require_writer)
):
    job = _get_job(db, job_id)
    try:
        resolve_job(db, job, data.outcome, user, utcnow())
    except CampaignError as exc:
        raise HTTPException(409, exc.message) from None
    db.commit()
    return {"ok": True}


@router.get("/audit", response_model=list[AuditOut])
def audit_log(
    limit: int = 100,
    before_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 500))
    if before_id:
        query = query.where(AuditEvent.id < before_id)
    return list(db.scalars(query))
