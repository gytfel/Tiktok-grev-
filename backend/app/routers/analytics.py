import csv
import io
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analytics, audit
from app.config import get_settings
from app.db import get_db
from app.deps import current_user, get_analytics_enqueuer, require_writer
from app.models import Account, PublicationJob, User, utcnow

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

PERIODS = (7, 30, 90)


@router.get("/overview")
def get_overview(days: int = 30, db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict:
    # Число из адреса приходит строкой, поэтому период проверяется явно, а не через Literal.
    if days not in PERIODS:
        raise HTTPException(422, "Период — 7, 30 или 90 дней")
    settings = get_settings()
    return {
        "enabled": settings.tiktok_analytics,
        "interval_hours": settings.analytics_interval_hours,
        **analytics.overview(db, days, utcnow()),
    }


@router.post("/refresh")
def refresh(
    db: Session = Depends(get_db),
    enqueue: Callable[[list[int]], None] = Depends(get_analytics_enqueuer),
    user: User = Depends(require_writer),
) -> dict:
    if not get_settings().tiktok_analytics:
        raise HTTPException(400, "Аналитика выключена: включите TIKTOK_ANALYTICS в .env")
    ids = analytics.eligible_account_ids(db)
    enqueue(ids)
    audit.record(db, user, "analytics.refresh", "analytics", None, {"accounts": len(ids)})
    db.commit()
    return {"queued": len(ids)}


def _cell(value) -> str:
    # Значение, начинающееся с =, +, - или @, Excel принял бы за формулу.
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _csv(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return out.getvalue()


@router.get("/export")
def export(
    kind: Literal["videos", "campaigns", "accounts"],
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> Response:
    if kind == "videos":
        jobs = db.scalars(
            select(PublicationJob)
            .where(PublicationJob.views.is_not(None))
            .order_by(PublicationJob.views.desc())
        )
        text = _csv(
            [
                "Кампания",
                "Аккаунт",
                "Ссылка",
                "Просмотры",
                "Лайки",
                "Комментарии",
                "Репосты",
                "Обновлено (UTC)",
            ],
            [
                [
                    j.campaign.title,
                    j.account.username or j.account.label,
                    f"https://www.tiktok.com/@{j.account.username}/video/{j.external_post_id}"
                    if j.account.username
                    else "",
                    j.views,
                    j.likes,
                    j.comments,
                    j.shares,
                    j.stats_updated_at.strftime("%Y-%m-%d %H:%M") if j.stats_updated_at else "",
                ]
                for j in jobs
            ],
        )
    elif kind == "campaigns":
        rows = analytics.campaign_table(db)
        text = _csv(
            [
                "Кампания",
                "Подтверждена (UTC)",
                "Видео со статистикой",
                "Просмотры",
                "Лайки",
                "Комментарии",
                "Репосты",
            ],
            [
                [
                    r["title"],
                    r["confirmed_at"].strftime("%Y-%m-%d %H:%M"),
                    r["videos"],
                    r["views"],
                    r["likes"],
                    r["comments"],
                    r["shares"],
                ]
                for r in rows
            ],
        )
    else:
        today = utcnow().date()
        rows = analytics.account_table(db, today, today)
        labels = {a.id: a.label for a in db.scalars(select(Account))}
        text = _csv(
            [
                "Аккаунт",
                "Название в панели",
                "Подписчики",
                "Лайки",
                "Видео в профиле",
                "Просмотры опубликованных",
            ],
            [
                [
                    r["username"] or "",
                    labels.get(r["id"], ""),
                    r["followers"],
                    r["likes"],
                    r["videos"],
                    r["views"],
                ]
                for r in rows
            ],
        )
    # BOM, чтобы Excel открыл кириллицу без вопросов.
    return Response(
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tiktok-{kind}.csv"'},
    )
