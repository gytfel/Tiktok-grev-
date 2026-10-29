"""Какие уведомления отправить: сводка по кампаниям, а не сообщение на каждое задание.

Отметки notified_at ставятся до отправки: если push-сервис недоступен, уведомление
потеряется, но не будет приходить повторно каждые несколько секунд.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignStatus, JobStatus, PublicationJob
from app.push import PushMessage

ATTENTION = (JobStatus.FAILED, JobStatus.NEEDS_REAUTH, JobStatus.NEEDS_REVIEW)
ATTENTION_LABELS = {
    JobStatus.FAILED: "ошибка",
    JobStatus.NEEDS_REAUTH: "нужна авторизация",
    JobStatus.NEEDS_REVIEW: "ручная проверка",
}


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(PublicationJob.status, func.count())
        .where(PublicationJob.campaign_id == campaign_id)
        .group_by(PublicationJob.status)
    )
    return dict(rows.all())


def collect(db: Session, now: datetime) -> list[tuple[str, PushMessage]]:
    messages: list[tuple[str, PushMessage]] = []

    finished = db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.COMPLETED, Campaign.notified_at.is_(None))
    ).all()
    for campaign in finished:
        counts = _counts(db, campaign.id)
        total = sum(counts.values())
        published = counts.get(JobStatus.PUBLISHED, 0)
        problems = sum(counts.get(s, 0) for s in ATTENTION)
        body = f"Опубликовано {published} из {total}"
        if problems:
            body += f", требуют внимания: {problems}"
        messages.append(
            (
                "campaign_finished",
                PushMessage(
                    title=f"Кампания «{campaign.title}» завершена",
                    body=body,
                    url=f"/campaigns/{campaign.id}",
                    tag=f"campaign-{campaign.id}",
                ),
            )
        )
        campaign.notified_at = now
        # Проблемы уже перечислены в сводке — отдельное уведомление о них не нужно.
        db.execute(
            update(PublicationJob)
            .where(
                PublicationJob.campaign_id == campaign.id,
                PublicationJob.status.in_(ATTENTION),
                PublicationJob.attention_notified_at.is_(None),
            )
            .values(attention_notified_at=now)
        )

    rows = db.execute(
        select(PublicationJob.id, PublicationJob.campaign_id, PublicationJob.status).where(
            PublicationJob.status.in_(ATTENTION),
            PublicationJob.attention_notified_at.is_(None),
        )
    ).all()
    by_campaign: dict[int, dict[str, int]] = {}
    for _, campaign_id, status in rows:
        counts = by_campaign.setdefault(campaign_id, {})
        counts[status] = counts.get(status, 0) + 1
    if rows:
        db.execute(
            update(PublicationJob)
            .where(PublicationJob.id.in_([r[0] for r in rows]))
            .values(attention_notified_at=now)
        )
    for campaign_id, counts in by_campaign.items():
        campaign = db.get(Campaign, campaign_id)
        n = sum(counts.values())
        requires = _plural(n, "задание требует", "задания требуют", "заданий требуют")
        details = ", ".join(f"{ATTENTION_LABELS[s]}: {c}" for s, c in counts.items())
        messages.append(
            (
                "attention",
                PushMessage(
                    title=f"«{campaign.title}»: {n} {requires} внимания",
                    body=details,
                    url=f"/campaigns/{campaign_id}",
                    tag=f"campaign-{campaign_id}-attention",
                ),
            )
        )

    db.commit()
    return messages
