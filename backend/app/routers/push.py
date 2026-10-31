from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import push
from app.db import get_db
from app.deps import current_user
from app.models import PushSubscription, User

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class Preferences(BaseModel):
    campaign_finished: bool = True
    attention: bool = True
    reminder: bool = True
    # Сбои системы приходят только администраторам и операторам.
    system: bool = True


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: SubscriptionKeys
    preferences: Preferences = Preferences()


class EndpointIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)


def _own(db: Session, user: User, endpoint: str) -> PushSubscription | None:
    return db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id
        )
    ).scalar_one_or_none()


@router.get("/config")
def config(db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict:
    return {"public_key": push.public_key(push.get_vapid(db))}


@router.post("/subscriptions")
def subscribe(
    data: SubscriptionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if not push.is_push_service_endpoint(data.endpoint):
        raise HTTPException(422, "Адрес подписки не относится к известному сервису уведомлений")
    sub = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == data.endpoint)
    ).scalar_one_or_none()
    if sub is None:
        sub = PushSubscription(endpoint=data.endpoint)
        db.add(sub)
    # Устройство принадлежит тому, кто вошёл в панель последним.
    sub.user_id = user.id
    sub.p256dh = data.keys.p256dh
    sub.auth = data.keys.auth
    sub.user_agent = request.headers.get("user-agent", "")[:255]
    sub.preferences = data.preferences.model_dump()
    db.commit()
    return {"subscribed": True, "preferences": sub.preferences}


@router.post("/subscriptions/lookup")
def lookup(data: EndpointIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    sub = _own(db, user, data.endpoint)
    if sub is None:
        return {"subscribed": False, "preferences": Preferences().model_dump()}
    return {"subscribed": True, "preferences": {**Preferences().model_dump(), **(sub.preferences or {})}}


@router.post("/subscriptions/delete")
def unsubscribe(data: EndpointIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    sub = _own(db, user, data.endpoint)
    if sub is not None:
        db.delete(sub)
        db.commit()
    return {"subscribed": False}


@router.post("/test")
def test(data: EndpointIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    sub = _own(db, user, data.endpoint)
    if sub is None:
        raise HTTPException(404, "Это устройство не подписано на уведомления")
    sent = push.send(
        db,
        push.PushMessage(
            title="Проверка уведомлений",
            body="Уведомления на этом устройстве работают.",
            url="/notifications",
            tag="test",
        ),
        [sub],
    )
    if not sent:
        raise HTTPException(502, "Push-сервис не принял уведомление. Попробуйте подписаться заново.")
    return {"sent": sent}
