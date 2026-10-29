import base64
import json
import os

import http_ece
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app import notifications, publishing, push
from app.models import JobStatus, PublicationJob, PushSubscription
from tests.conftest import confirm, connect, make_media
from tests.test_publishing import drive, jobs_of


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Browser:
    """Ключи подписки, как их создаёт браузер в pushManager.subscribe()."""

    def __init__(self, endpoint="https://push.example.com/send/abc"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def subscription_json(self):
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64url(public), "auth": b64url(self.auth)}}

    def decrypt(self, body: bytes) -> dict:
        return json.loads(
            http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth, version="aes128gcm")
        )


class PushService:
    """Подменяет HTTP-запросы pywebpush к push-сервису."""

    def __init__(self, status=201):
        self.status = status
        self.requests = []

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.requests.append({"url": url, "data": data, "headers": headers})
        response = requests.Response()
        response.status_code = self.status
        response._content = b""
        return response


def add_subscription(db, user, browser, **prefs):
    data = browser.subscription_json()
    sub = PushSubscription(
        user_id=user.id,
        endpoint=data["endpoint"],
        p256dh=data["keys"]["p256dh"],
        auth=data["keys"]["auth"],
        preferences=prefs,
    )
    db.add(sub)
    db.commit()
    return sub


def test_vapid_keys_are_created_once(db):
    first = push.public_key(push.get_vapid(db))
    assert push.public_key(push.get_vapid(db)) == first
    assert len(base64.urlsafe_b64decode(first + "=")) == 65


def test_message_is_encrypted_for_the_device(db, admin):
    browser = Browser()
    sub = add_subscription(db, admin, browser)
    service = PushService()

    sent = push.send(db, push.PushMessage("Заголовок", "Текст", "/campaigns/1", "t"), [sub], session=service)

    assert sent == 1
    req = service.requests[0]
    assert req["url"] == browser.endpoint
    assert req["headers"]["Authorization"].startswith("vapid t=")
    assert int(req["headers"]["TTL"]) == push.TTL_S
    assert browser.decrypt(req["data"]) == {
        "title": "Заголовок",
        "body": "Текст",
        "url": "/campaigns/1",
        "tag": "t",
    }
    assert db.get(PushSubscription, sub.id).last_success_at is not None


def test_expired_subscription_is_removed(db, admin):
    gone = add_subscription(db, admin, Browser("https://push.example.com/gone"))
    assert push.send(db, push.PushMessage("a", "b"), [gone], session=PushService(410)) == 0
    assert db.scalar(select(PushSubscription)) is None


def test_temporary_failure_keeps_subscription(db, admin):
    sub = add_subscription(db, admin, Browser())
    assert push.send(db, push.PushMessage("a", "b"), [sub], session=PushService(500)) == 0
    kept = db.get(PushSubscription, sub.id)
    assert kept is not None and kept.last_error


def test_preferences_filter_events(db, admin):
    add_subscription(db, admin, Browser("https://push.example.com/all"))
    add_subscription(db, admin, Browser("https://push.example.com/quiet"), attention=False)
    assert len(push.subscriptions_for(db, "campaign_finished")) == 2
    assert [s.endpoint for s in push.subscriptions_for(db, "attention")] == ["https://push.example.com/all"]


# --- какие уведомления отправляются ------------------------------------------------------


def test_campaign_finished_is_reported_once(db, fake, tt, settings, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo", "charlie")]
    campaign = confirm(db, admin, make_media(db), accounts, clock)
    fake.fail("/v2/post/publish/video/init/", (403, "spam_risk_too_many_posts"))
    drive(db, tt, settings, clock, rounds=4)

    messages = notifications.collect(db, clock.now)
    assert len(messages) == 1
    event, message = messages[0]
    assert event == "campaign_finished"
    assert message.title == "Кампания «Тестовая кампания» завершена"
    assert message.body == "Опубликовано 2 из 3, требуют внимания: 1"
    assert message.url == f"/campaigns/{campaign.id}"
    assert notifications.collect(db, clock.now) == []


def test_attention_is_grouped_per_campaign(db, fake, tt, settings, clock, admin):
    accounts = [connect(db, fake, tt, n, clock) for n in ("alpha", "bravo", "charlie")]
    campaign = confirm(db, admin, make_media(db), accounts, clock)
    fake.fail(
        "/v2/post/publish/video/init/", (403, "spam_risk_too_many_posts"), (401, "access_token_invalid")
    )
    fake.processing_delay_s = 10**9  # третье задание остаётся в обработке, кампания не завершена

    drive(db, tt, settings, clock, rounds=3)
    messages = notifications.collect(db, clock.now)

    assert [e for e, _ in messages] == ["attention"]
    message = messages[0][1]
    assert message.title == "«Тестовая кампания»: 2 задания требуют внимания"
    assert "ошибка: 1" in message.body and "нужна авторизация: 1" in message.body
    assert notifications.collect(db, clock.now) == []

    # После повтора новая ошибка снова попадает в уведомления.
    failed = next(j for j in jobs_of(db, campaign) if j.status == JobStatus.FAILED)
    publishing.retry_job(db, failed, admin, clock.now)
    db.commit()
    fake.fail("/v2/post/publish/video/init/", (403, "spam_risk_too_many_posts"))
    drive(db, tt, settings, clock, rounds=2)
    messages = notifications.collect(db, clock.now)
    assert len(messages) == 1 and "1 задание требует" in messages[0][1].title


def test_rerun_campaign_is_reported_again(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    fake.fail("/v2/post/publish/video/init/", (403, "spam_risk_too_many_posts"))
    drive(db, tt, settings, clock, rounds=3)
    assert len(notifications.collect(db, clock.now)) == 1

    publishing.retry_job(db, jobs_of(db, campaign)[0], admin, clock.now)
    db.commit()
    drive(db, tt, settings, clock, rounds=3)
    messages = notifications.collect(db, clock.now)
    assert [m.body for _, m in messages] == ["Опубликовано 1 из 1"]
    assert db.scalar(select(PublicationJob)).status == JobStatus.PUBLISHED


# --- API ---------------------------------------------------------------------------------


def test_subscription_api(admin_client, monkeypatch):
    browser = Browser()
    assert len(admin_client.get("/api/push/config").json()["public_key"]) == 87

    payload = {**browser.subscription_json(), "preferences": {"campaign_finished": True, "attention": False}}
    assert admin_client.post("/api/push/subscriptions", json=payload).json()["subscribed"]
    lookup = admin_client.post("/api/push/subscriptions/lookup", json={"endpoint": browser.endpoint}).json()
    assert lookup == {"subscribed": True, "preferences": {"campaign_finished": True, "attention": False}}

    sent = []
    monkeypatch.setattr(push, "webpush", lambda info, **kw: sent.append((info, kw)))
    assert admin_client.post("/api/push/test", json={"endpoint": browser.endpoint}).json() == {"sent": 1}
    assert sent[0][0]["endpoint"] == browser.endpoint
    assert json.loads(sent[0][1]["data"])["url"] == "/notifications"

    admin_client.post("/api/push/subscriptions/delete", json={"endpoint": browser.endpoint})
    assert not admin_client.post(
        "/api/push/subscriptions/lookup", json={"endpoint": browser.endpoint}
    ).json()["subscribed"]
    assert admin_client.post("/api/push/test", json={"endpoint": browser.endpoint}).status_code == 404


def test_subscription_requires_https_endpoint(admin_client):
    payload = Browser("http://push.example.com/x").subscription_json()
    assert admin_client.post("/api/push/subscriptions", json=payload).status_code == 422


def test_push_requires_login(client):
    assert client.get("/api/push/config").status_code == 401
