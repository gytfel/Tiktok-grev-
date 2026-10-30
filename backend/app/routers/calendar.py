"""Календарь: кампании по времени старта, напоминания и итоги публикаций по дням."""

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user, require_writer
from app.models import Campaign, JobStatus, PublicationJob, Reminder, User, utcnow
from app.publishing import CampaignError, planned_campaign_ids, reschedule_campaign
from app.routers.campaigns import _counts
from app.routers.comments import ReminderOut

router = APIRouter(prefix="/api", tags=["calendar"])

MAX_RANGE = timedelta(days=62)
ATTENTION = (JobStatus.FAILED, JobStatus.NEEDS_REAUTH, JobStatus.NEEDS_REVIEW)


class CalendarCampaign(BaseModel):
    id: int
    title: str
    status: str
    start_at: datetime
    media_filename: str
    total: int
    published: int
    problems: int
    planned: bool


class DaySummary(BaseModel):
    published: int = 0
    problems: int = 0


class CalendarOut(BaseModel):
    campaigns: list[CalendarCampaign]
    reminders: list[ReminderOut]
    days: dict[str, DaySummary]


class RescheduleIn(BaseModel):
    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Укажите время с часовым поясом")
        return value


@router.get("/calendar", response_model=CalendarOut)
def calendar(
    start: datetime,
    end: datetime,
    tz: str = "UTC",
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, "Укажите границы периода с часовым поясом")
    if not timedelta(0) < end - start <= MAX_RANGE:
        raise HTTPException(422, "Период должен быть не длиннее двух месяцев")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Неизвестный часовой пояс") from None

    campaigns = list(
        db.scalars(
            select(Campaign)
            .where(Campaign.start_at >= start, Campaign.start_at < end)
            .order_by(Campaign.start_at)
        )
    )
    counts = _counts(db, [c.id for c in campaigns])
    planned = planned_campaign_ids(db, campaigns, utcnow())
    reminders = list(
        db.scalars(
            select(Reminder).where(Reminder.due_at >= start, Reminder.due_at < end).order_by(Reminder.due_at)
        )
    )

    # Итоги по дням считаются в часовом поясе пользователя.
    days: dict[str, DaySummary] = defaultdict(DaySummary)
    published = db.scalars(
        select(PublicationJob.published_at).where(
            PublicationJob.published_at >= start, PublicationJob.published_at < end
        )
    )
    for moment in published:
        days[moment.astimezone(zone).date().isoformat()].published += 1
    problems = db.scalars(
        select(PublicationJob.updated_at).where(
            PublicationJob.status.in_(ATTENTION),
            PublicationJob.updated_at >= start,
            PublicationJob.updated_at < end,
        )
    )
    for moment in problems:
        days[moment.astimezone(zone).date().isoformat()].problems += 1

    return CalendarOut(
        campaigns=[
            CalendarCampaign(
                id=c.id,
                title=c.title,
                status=c.status,
                start_at=c.start_at,
                media_filename=c.media.original_filename,
                total=sum(counts[c.id].values()),
                published=counts[c.id].get(JobStatus.PUBLISHED, 0),
                problems=sum(counts[c.id].get(s, 0) for s in ATTENTION),
                planned=c.id in planned,
            )
            for c in campaigns
        ],
        reminders=[ReminderOut.model_validate(r) for r in reminders],
        days=dict(days),
    )


@router.post("/campaigns/{campaign_id}/reschedule")
def reschedule(
    campaign_id: int, data: RescheduleIn, db: Session = Depends(get_db), user: User = Depends(require_writer)
) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Кампания не найдена")
    try:
        reschedule_campaign(db, campaign, data.start_at, user, utcnow())
    except CampaignError as exc:
        raise HTTPException(409, exc.message) from None
    db.commit()
    return {"ok": True, "start_at": campaign.start_at}
