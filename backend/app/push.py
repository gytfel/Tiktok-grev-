"""Web Push (VAPID): уведомления на телефон и в браузер.

Ключи VAPID создаются автоматически при первом обращении и хранятся в базе зашифрованными,
поэтому ничего настраивать не нужно. Если ключи потерять, устройства придётся подписать заново.
"""

import base64
import json
from dataclasses import asdict, dataclass

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSecret, PushSubscription, utcnow
from app.security import decrypt_secret, encrypt_secret

VAPID_KEY = "vapid_private_pem"
EVENTS = ("campaign_finished", "attention")
TTL_S = 24 * 3600


@dataclass
class PushMessage:
    title: str
    body: str
    url: str = "/"
    tag: str | None = None


def _generate_private_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def get_vapid(db: Session) -> Vapid02:
    row = db.get(AppSecret, VAPID_KEY)
    if row is None:
        pem = _generate_private_pem()
        db.add(AppSecret(key=VAPID_KEY, value_enc=encrypt_secret(pem)))
        try:
            db.commit()
        except IntegrityError:
            # Ключ одновременно создал другой процесс — берём его.
            db.rollback()
            row = db.get(AppSecret, VAPID_KEY)
    if row is not None:
        pem = decrypt_secret(row.value_enc)
    return Vapid02.from_pem(pem.encode())


def public_key(vapid: Vapid02) -> str:
    """Ключ applicationServerKey для pushManager.subscribe() в формате base64url."""
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subject() -> str:
    settings = get_settings()
    if settings.vapid_subject:
        return settings.vapid_subject
    if settings.public_base_url.startswith("https://"):
        return settings.public_base_url.rstrip("/")
    return "mailto:admin@example.com"


def send(
    db: Session,
    message: PushMessage,
    subscriptions: list[PushSubscription],
    session: requests.Session | None = None,
) -> int:
    """Отправляет сообщение на подписки; недействительные подписки удаляются."""
    if not subscriptions:
        return 0
    vapid = get_vapid(db)
    payload = json.dumps(asdict(message), ensure_ascii=False)
    sent = 0
    for sub in subscriptions:
        try:
            webpush(
                {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=vapid,
                # Словарь claims pywebpush дополняет на месте (aud, exp), поэтому он свой для каждой отправки.
                vapid_claims={"sub": subject()},
                ttl=TTL_S,
                timeout=10,
                requests_session=session,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                db.delete(sub)
            else:
                sub.last_error = str(exc)[:500]
        except requests.RequestException as exc:
            sub.last_error = str(exc)[:500]
        else:
            sub.last_success_at = utcnow()
            sub.last_error = None
            sent += 1
    db.commit()
    return sent


def subscriptions_for(db: Session, event: str) -> list[PushSubscription]:
    subs = db.scalars(select(PushSubscription)).all()
    return [s for s in subs if (s.preferences or {}).get(event, True)]
