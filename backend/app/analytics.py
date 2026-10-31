"""Аналитика через Display API TikTok: показатели своих видео и аккаунтов.

TikTok отдаёт статистику только публичных видео (их id приходит после публикации) и только
аккаунтам, выдавшим разрешения user.info.stats и video.list. Сбор идёт по расписанию;
для графиков сохраняются дневные срезы по кампаниям и аккаунтам.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts_service import NeedsReauth, ensure_access_token
from app.config import ANALYTICS_SCOPES
from app.models import (
    Account,
    AccountStatsDaily,
    AccountStatus,
    Campaign,
    CampaignStatsDaily,
    JobStatus,
    PublicationJob,
)
from app.tiktok import TikTokClient, TikTokError

METRICS = ("views", "likes", "comments", "shares")
VIDEO_FIELDS = {
    "views": "view_count",
    "likes": "like_count",
    "comments": "comment_count",
    "shares": "share_count",
}
BATCH = 20


def account_scopes(account: Account) -> set[str]:
    return {s.strip() for s in (account.scopes or "").split(",") if s.strip()}


def has_analytics_scopes(account: Account) -> bool:
    return set(ANALYTICS_SCOPES) <= account_scopes(account)


def _upsert(db: Session, model, keys: dict[str, Any], values: dict[str, Any]) -> None:
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(model).values(**keys, **values)
    db.execute(stmt.on_conflict_do_update(index_elements=list(keys), set_=values))


def collect_account(db: Session, tt: TikTokClient, account_id: int, now: datetime) -> set[int]:
    """Обновляет статистику аккаунта и его видео. Возвращает id затронутых кампаний."""
    account = db.get(Account, account_id)
    if account is None or account.connection_status != AccountStatus.CONNECTED:
        return set()
    scopes = account_scopes(account)
    if not scopes & set(ANALYTICS_SCOPES):
        return set()
    try:
        token = ensure_access_token(db, account, tt, now)
    except (NeedsReauth, TikTokError):
        db.rollback()
        return set()

    today = now.date()
    if "user.info.stats" in scopes:
        try:
            info = tt.user_stats(token)
        except TikTokError:
            info = None
        if info:
            _upsert(
                db,
                AccountStatsDaily,
                {"account_id": account.id, "day": today},
                {
                    "followers": int(info.get("follower_count") or 0),
                    "following": int(info.get("following_count") or 0),
                    "likes": int(info.get("likes_count") or 0),
                    "videos": int(info.get("video_count") or 0),
                    "updated_at": now,
                },
            )

    touched: set[int] = set()
    if "video.list" in scopes:
        jobs = list(
            db.scalars(
                select(PublicationJob).where(
                    PublicationJob.account_id == account.id,
                    PublicationJob.status == JobStatus.PUBLISHED,
                    PublicationJob.external_post_id.is_not(None),
                )
            )
        )
        for start in range(0, len(jobs), BATCH):
            chunk = jobs[start : start + BATCH]
            try:
                videos = tt.query_videos(token, [j.external_post_id for j in chunk])
            except TikTokError:
                break
            by_id = {str(v.get("id")): v for v in videos}
            for job in chunk:
                video = by_id.get(job.external_post_id)
                if video is None:
                    continue
                for metric, field in VIDEO_FIELDS.items():
                    setattr(job, metric, int(video.get(field) or 0))
                job.stats_updated_at = now
                touched.add(job.campaign_id)
    db.commit()
    rollup_campaigns(db, touched, now)
    return touched


def rollup_campaigns(db: Session, campaign_ids: set[int], now: datetime) -> None:
    """Сохраняет суммы по видео кампаний в срез текущего дня."""
    if not campaign_ids:
        return
    rows = db.execute(
        select(
            PublicationJob.campaign_id,
            *[func.coalesce(func.sum(getattr(PublicationJob, m)), 0) for m in METRICS],
            func.count(PublicationJob.views),
        )
        .where(PublicationJob.campaign_id.in_(campaign_ids), PublicationJob.views.is_not(None))
        .group_by(PublicationJob.campaign_id)
    ).all()
    for campaign_id, views, likes, comments, shares, videos in rows:
        _upsert(
            db,
            CampaignStatsDaily,
            {"campaign_id": campaign_id, "day": now.date()},
            {
                "views": int(views),
                "likes": int(likes),
                "comments": int(comments),
                "shares": int(shares),
                "videos": int(videos),
                "updated_at": now,
            },
        )
    db.commit()


def eligible_account_ids(db: Session) -> list[int]:
    accounts = db.scalars(select(Account).where(Account.connection_status == AccountStatus.CONNECTED))
    return [a.id for a in accounts if account_scopes(a) & set(ANALYTICS_SCOPES)]


# --- сводка для страницы аналитики -------------------------------------------------------


def _carry_forward(
    rows, key, fields, days: list[date], before: date, from_zero: frozenset[int] = frozenset()
) -> tuple[dict, list[dict]]:
    """Сумма по объектам на каждый день окна; пропущенные дни берут последнее известное значение.

    До первого среза объект считается нулём, только если он и правда начинал с нуля (новая
    кампания из from_zero). Иначе — первым известным значением: подключение аккаунта или включение
    аналитики не должно выглядеть скачком. Точка отсчёта для прироста — значение на день до окна.
    """
    by_object: dict[int, list] = defaultdict(list)
    for row in rows:
        by_object[getattr(row, key)].append(row)
    baseline = {f: 0 for f in fields}
    series = [{f: 0 for f in fields} for _ in days]
    for object_id, history in by_object.items():
        history.sort(key=lambda r: r.day)
        i = 0
        last = None if object_id in from_zero else history[0]
        while i < len(history) and history[i].day < before:
            last = history[i]
            i += 1
        if last is not None:
            for f in fields:
                baseline[f] += getattr(last, f)
        for d_index, day in enumerate(days):
            while i < len(history) and history[i].day <= day:
                last = history[i]
                i += 1
            if last is not None:
                for f in fields:
                    series[d_index][f] += getattr(last, f)
    return baseline, series


def _fresh_campaigns(db: Session, rows: list[CampaignStatsDaily]) -> frozenset[int]:
    """Кампании, которые начали собирать статистику сразу после старта: их просмотры шли с нуля.

    Кампании старше аналитики сюда не входят — их накопленные просмотры не прирост первого дня.
    """
    first_day: dict[int, date] = {}
    for row in rows:
        first_day[row.campaign_id] = min(first_day.get(row.campaign_id, row.day), row.day)
    starts = db.execute(select(Campaign.id, Campaign.start_at).where(Campaign.id.in_(first_day)))
    return frozenset(cid for cid, start_at in starts if first_day[cid] <= start_at.date() + timedelta(days=1))


def overview(db: Session, days: int, now: datetime) -> dict[str, Any]:
    end = now.date()
    start = end - timedelta(days=days - 1)
    window = [start + timedelta(days=i) for i in range(days)]

    campaign_rows = list(db.scalars(select(CampaignStatsDaily).where(CampaignStatsDaily.day <= end)))
    account_rows = list(db.scalars(select(AccountStatsDaily).where(AccountStatsDaily.day <= end)))
    c_base, c_series = _carry_forward(
        campaign_rows, "campaign_id", METRICS, window, start, _fresh_campaigns(db, campaign_rows)
    )
    a_base, a_series = _carry_forward(account_rows, "account_id", ("followers",), window, start)

    series = []
    previous_views = c_base["views"]
    for day, c, a in zip(window, c_series, a_series, strict=True):
        series.append(
            {
                "day": day.isoformat(),
                **c,
                "views_gain": max(c["views"] - previous_views, 0),
                "followers": a["followers"],
            }
        )
        previous_views = c["views"]

    last = series[-1]
    totals = {**{m: last[m] for m in METRICS}, "followers": last["followers"]}
    deltas = {
        **{m: last[m] - c_base[m] for m in METRICS},
        "followers": last["followers"] - a_base["followers"],
    }

    top_campaigns = campaign_table(db, limit=10)

    accounts = account_table(db, start, end)[:20]
    connected = list(db.scalars(select(Account).where(Account.connection_status == AccountStatus.CONNECTED)))
    last_collected = max(
        [r.updated_at for r in campaign_rows] + [r.updated_at for r in account_rows], default=None
    )
    return {
        "totals": totals,
        "deltas": deltas,
        "series": series,
        "top_campaigns": top_campaigns,
        "top_accounts": accounts,
        "accounts_connected": len(connected),
        "accounts_missing_scopes": sum(1 for a in connected if not has_analytics_scopes(a)),
        "videos_tracked": db.scalar(select(func.count()).where(PublicationJob.views.is_not(None))),
        "videos_private": db.scalar(
            select(func.count()).where(
                PublicationJob.status == JobStatus.PUBLISHED, PublicationJob.external_post_id.is_(None)
            )
        ),
        "last_collected_at": last_collected,
    }


def campaign_table(db: Session, limit: int | None = None) -> list[dict[str, Any]]:
    """Кампании с текущими суммами по видео, по убыванию просмотров."""
    sums = [func.coalesce(func.sum(getattr(PublicationJob, m)), 0) for m in METRICS]
    query = (
        select(Campaign.id, Campaign.title, Campaign.confirmed_at, *sums, func.count(PublicationJob.views))
        .join(PublicationJob, PublicationJob.campaign_id == Campaign.id)
        .where(PublicationJob.views.is_not(None))
        .group_by(Campaign.id, Campaign.title, Campaign.confirmed_at)
        .order_by(func.sum(PublicationJob.views).desc())
    )
    if limit:
        query = query.limit(limit)
    return [
        {
            "id": cid,
            "title": title,
            "confirmed_at": confirmed_at,
            "views": int(v),
            "likes": int(lk),
            "comments": int(cm),
            "shares": int(sh),
            "videos": n,
        }
        for cid, title, confirmed_at, v, lk, cm, sh, n in db.execute(query)
    ]


def account_table(db: Session, start: date, end: date) -> list[dict[str, Any]]:
    """Аккаунты с последними показателями и приростом подписчиков за период, по убыванию подписчиков."""
    latest: dict[int, AccountStatsDaily] = {}
    baseline: dict[int, AccountStatsDaily] = {}
    rows = db.scalars(
        select(AccountStatsDaily).where(AccountStatsDaily.day <= end).order_by(AccountStatsDaily.day)
    )
    for row in rows:
        latest[row.account_id] = row
        # До начала периода — последнее значение; если его нет, первое значение в периоде.
        if row.day < start or row.account_id not in baseline:
            baseline[row.account_id] = row
    views = dict(
        db.execute(
            select(PublicationJob.account_id, func.sum(PublicationJob.views))
            .where(PublicationJob.views.is_not(None))
            .group_by(PublicationJob.account_id)
        ).all()
    )
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(latest)))}
    result = []
    for account_id, row in latest.items():
        account = accounts.get(account_id)
        if account is None:
            continue
        base = baseline.get(account_id)
        result.append(
            {
                "id": account_id,
                "label": account.label,
                "username": account.username,
                "followers": row.followers,
                "followers_gain": row.followers - base.followers if base else 0,
                "likes": row.likes,
                "videos": row.videos,
                "views": int(views.get(account_id) or 0),
            }
        )
    result.sort(key=lambda r: r["followers"], reverse=True)
    return result
