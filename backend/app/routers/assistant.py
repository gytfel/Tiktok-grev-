"""Шаблоны текстов и AI-помощник."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, audit
from app.config import get_settings
from app.db import get_db
from app.deps import current_user, get_ai, require_writer
from app.models import TextTemplate, User
from app.schemas import ORM

router = APIRouter(prefix="/api", tags=["assistant"])

Kind = Literal["caption", "hashtags", "reply"]


class TemplateIn(BaseModel):
    kind: Kind
    category: str = Field(default="", max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class TemplateUpdate(BaseModel):
    category: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)


class TemplateOut(ORM):
    id: int
    kind: str
    category: str
    title: str
    body: str
    updated_at: datetime


class GenerateIn(BaseModel):
    task: Literal["caption", "hashtags", "ideas", "reply"]
    topic: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=2200)
    comment: str = Field(default="", max_length=1000)
    tone: str = Field(default="", max_length=100)
    language: str = Field(default="русский", max_length=50)
    count: int = Field(default=5, ge=1, le=ai.MAX_COUNT)


def _get_template(db: Session, template_id: int) -> TextTemplate:
    template = db.get(TextTemplate, template_id)
    if template is None:
        raise HTTPException(404, "Шаблон не найден")
    return template


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(kind: Kind | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    query = select(TextTemplate).order_by(TextTemplate.category, TextTemplate.title)
    if kind:
        query = query.where(TextTemplate.kind == kind)
    return list(db.scalars(query))


@router.post("/templates", response_model=TemplateOut)
def create_template(data: TemplateIn, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    template = TextTemplate(**data.model_dump())
    db.add(template)
    db.flush()
    audit.record(db, user, "template.created", "template", template.id, {"kind": template.kind})
    db.commit()
    return template


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int,
    data: TemplateUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_writer),
):
    template = _get_template(db, template_id)
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(template, key, value)
    audit.record(db, user, "template.updated", "template", template.id)
    db.commit()
    return template


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    template = _get_template(db, template_id)
    audit.record(db, user, "template.deleted", "template", template.id, {"title": template.title})
    db.delete(template)
    db.commit()
    return {"ok": True}


@router.get("/ai/status")
def ai_status(_: User = Depends(current_user)) -> dict:
    settings = get_settings()
    return {"enabled": settings.ai_configured, "model": settings.ai_model if settings.ai_configured else None}


@router.post("/ai/generate")
def ai_generate(
    data: GenerateIn,
    db: Session = Depends(get_db),
    client: ai.AIClient | None = Depends(get_ai),
    user: User = Depends(require_writer),
) -> dict:
    if client is None:
        raise HTTPException(400, "AI не настроен: укажите AI_BASE_URL и AI_MODEL в .env")
    if data.task == "reply" and not data.comment.strip():
        raise HTTPException(422, "Нужен текст комментария, на который отвечаем")
    try:
        variants = ai.generate(client, ai.GenerateRequest(**data.model_dump()))
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from None
    audit.record(db, user, "ai.generated", "ai", None, {"task": data.task, "count": len(variants)})
    db.commit()
    return {"variants": variants}
