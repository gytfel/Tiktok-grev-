"""Comment Manager: черновики ответов на комментарии под своими публикациями и напоминания.

Панель не отправляет комментарии: человек копирует готовый ответ, открывает видео в TikTok
и публикует его сам, затем отмечает черновик как отправленный.
"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import current_user, require_writer
from app.models import (
    Account,
    CommentDraft,
    CommentDraftStatus,
    PublicationJob,
    Reminder,
    User,
    utcnow,
)

router = APIRouter(prefix="/api", tags=["comments"])

TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com")


def _check_video_url(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    parts = urlsplit(value)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in TIKTOK_HOSTS:
        raise ValueError("Ссылка должна вести на tiktok.com")
    return value


def _check_internal_link(value: str) -> str:
    value = value.strip()
    if value and (not value.startswith("/") or value.startswith("//") or "\\" in value):
        raise ValueError("Ссылка должна вести на страницу панели, например /comments/5")
    return value


def job_video_url(job: PublicationJob | None) -> str:
    if job is None or not job.external_post_id or not job.account.username:
        return ""
    return f"https://www.tiktok.com/@{job.account.username}/video/{job.external_post_id}"


# --- черновики ответов -------------------------------------------------------------------


class CommentIn(BaseModel):
    job_id: int | None = None
    account_id: int | None = None
    video_url: str = Field(default="", max_length=500)
    original_comment: str = Field(default="", max_length=2200)
    reply: str = Field(default="", max_length=2200)

    _url = field_validator("video_url")(_check_video_url)


class CommentUpdate(BaseModel):
    video_url: str | None = Field(default=None, max_length=500)
    original_comment: str | None = Field(default=None, max_length=2200)
    reply: str | None = Field(default=None, max_length=2200)
    status: Literal["draft", "sent", "archived"] | None = None

    @field_validator("video_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else _check_video_url(value)


class CommentOut(BaseModel):
    id: int
    job_id: int | None
    campaign_id: int | None
    campaign_title: str | None
    account_id: int | None
    account_label: str | None
    account_username: str | None
    video_url: str
    original_comment: str
    reply: str
    status: str
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


def _comment_out(c: CommentDraft) -> CommentOut:
    job = c.job
    account = c.account or (job.account if job else None)
    return CommentOut(
        id=c.id,
        job_id=c.job_id,
        campaign_id=job.campaign_id if job else None,
        campaign_title=job.campaign.title if job else None,
        account_id=account.id if account else None,
        account_label=account.label if account else None,
        account_username=account.username if account else None,
        video_url=c.video_url or job_video_url(job),
        original_comment=c.original_comment,
        reply=c.reply,
        status=c.status,
        created_at=c.created_at,
        updated_at=c.updated_at,
        sent_at=c.sent_at,
    )


def _get_comment(db: Session, comment_id: int) -> CommentDraft:
    comment = db.get(CommentDraft, comment_id)
    if comment is None:
        raise HTTPException(404, "Черновик не найден")
    return comment


@router.get("/comments", response_model=list[CommentOut])
def list_comments(
    status: Literal["draft", "sent", "archived"] | None = None,
    job_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    query = select(CommentDraft).order_by(CommentDraft.updated_at.desc()).limit(500)
    if status:
        query = query.where(CommentDraft.status == status)
    if job_id:
        query = query.where(CommentDraft.job_id == job_id)
    return [_comment_out(c) for c in db.scalars(query).unique()]


@router.get("/comments/{comment_id}", response_model=CommentOut)
def get_comment(comment_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _comment_out(_get_comment(db, comment_id))


@router.post("/comments", response_model=CommentOut)
def create_comment(data: CommentIn, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    account_id = data.account_id
    if data.job_id is not None:
        job = db.get(PublicationJob, data.job_id)
        if job is None:
            raise HTTPException(404, "Публикация не найдена")
        account_id = job.account_id
    elif account_id is not None and db.get(Account, account_id) is None:
        raise HTTPException(404, "Аккаунт не найден")
    comment = CommentDraft(
        job_id=data.job_id,
        account_id=account_id,
        video_url=data.video_url,
        original_comment=data.original_comment,
        reply=data.reply,
        created_by=user.id,
    )
    db.add(comment)
    db.flush()
    audit.record(db, user, "comment.created", "comment", comment.id, {"job_id": data.job_id})
    db.commit()
    db.refresh(comment)
    return _comment_out(comment)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(
    comment_id: int, data: CommentUpdate, db: Session = Depends(get_db), user: User = Depends(require_writer)
):
    comment = _get_comment(db, comment_id)
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(comment, key, value)
    if changes.get("status") == CommentDraftStatus.SENT and comment.sent_at is None:
        comment.sent_at = utcnow()
    elif "status" in changes and changes["status"] != CommentDraftStatus.SENT:
        comment.sent_at = None
    if "status" in changes:
        audit.record(db, user, f"comment.{changes['status']}", "comment", comment.id)
    db.commit()
    db.refresh(comment)
    return _comment_out(comment)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    comment = _get_comment(db, comment_id)
    audit.record(db, user, "comment.deleted", "comment", comment.id)
    db.delete(comment)
    db.commit()
    return {"ok": True}


# --- напоминания -------------------------------------------------------------------------


class ReminderIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)
    due_at: datetime
    link: str = Field(default="", max_length=500)
    comment_draft_id: int | None = None

    _link = field_validator("link")(_check_internal_link)

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Укажите время с часовым поясом")
        return value


class ReminderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    link: str | None = Field(default=None, max_length=500)

    @field_validator("link")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        return None if value is None else _check_internal_link(value)

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Укажите время с часовым поясом")
        return value


class ReminderOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    note: str
    due_at: datetime
    link: str
    comment_draft_id: int | None
    created_at: datetime
    done_at: datetime | None
    notified_at: datetime | None


def _get_reminder(db: Session, reminder_id: int) -> Reminder:
    reminder = db.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(404, "Напоминание не найдено")
    return reminder


@router.get("/reminders", response_model=list[ReminderOut])
def list_reminders(
    state: Literal["open", "done", "all"] = "open",
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    query = select(Reminder).order_by(Reminder.due_at).limit(500)
    if state == "open":
        query = query.where(Reminder.done_at.is_(None))
    elif state == "done":
        query = query.where(Reminder.done_at.is_not(None)).order_by(None).order_by(Reminder.done_at.desc())
    return list(db.scalars(query))


@router.post("/reminders", response_model=ReminderOut)
def create_reminder(data: ReminderIn, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    if data.comment_draft_id is not None:
        _get_comment(db, data.comment_draft_id)
    reminder = Reminder(**data.model_dump(), created_by=user.id)
    if not reminder.link and data.comment_draft_id:
        reminder.link = f"/comments?open={data.comment_draft_id}"
    db.add(reminder)
    db.flush()
    audit.record(db, user, "reminder.created", "reminder", reminder.id, {"due_at": data.due_at.isoformat()})
    db.commit()
    return reminder


@router.patch("/reminders/{reminder_id}", response_model=ReminderOut)
def update_reminder(
    reminder_id: int,
    data: ReminderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_writer),
):
    reminder = _get_reminder(db, reminder_id)
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(reminder, key, value)
    if "due_at" in changes:
        # Перенесённое напоминание должно сработать снова.
        reminder.notified_at = None
    db.commit()
    return reminder


@router.post("/reminders/{reminder_id}/done", response_model=ReminderOut)
def complete_reminder(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    reminder = _get_reminder(db, reminder_id)
    reminder.done_at = reminder.done_at or utcnow()
    audit.record(db, user, "reminder.done", "reminder", reminder.id)
    db.commit()
    return reminder


@router.post("/reminders/{reminder_id}/reopen", response_model=ReminderOut)
def reopen_reminder(reminder_id: int, db: Session = Depends(get_db), _: User = Depends(require_writer)):
    reminder = _get_reminder(db, reminder_id)
    reminder.done_at = None
    db.commit()
    return reminder


@router.delete("/reminders/{reminder_id}")
def delete_reminder(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    reminder = _get_reminder(db, reminder_id)
    audit.record(db, user, "reminder.deleted", "reminder", reminder.id)
    db.delete(reminder)
    db.commit()
    return {"ok": True}
