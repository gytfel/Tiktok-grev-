from datetime import timedelta

import pytest

from app import notifications
from app.models import Reminder, utcnow
from tests.conftest import confirm, connect, make_media
from tests.test_publishing import drive, jobs_of


@pytest.fixture
def published_job(db, fake, tt, settings, clock, admin):
    account = connect(db, fake, tt, "alpha", clock)
    campaign = confirm(db, admin, make_media(db), [account], clock)
    drive(db, tt, settings, clock, rounds=3)
    return jobs_of(db, campaign)[0]


def test_reply_draft_linked_to_publication(admin_client, published_job):
    r = admin_client.post(
        "/api/comments",
        json={"job_id": published_job.id, "original_comment": "Где купить?", "reply": "Ссылка в профиле"},
    )
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["account_username"] == "alpha"
    assert draft["campaign_title"] == "Тестовая кампания"
    assert draft["video_url"] == f"https://www.tiktok.com/@alpha/video/{published_job.external_post_id}"
    assert draft["status"] == "draft"

    sent = admin_client.patch(f"/api/comments/{draft['id']}", json={"status": "sent"}).json()
    assert sent["status"] == "sent" and sent["sent_at"]
    back = admin_client.patch(f"/api/comments/{draft['id']}", json={"status": "draft"}).json()
    assert back["sent_at"] is None

    assert [c["id"] for c in admin_client.get(f"/api/comments?job_id={published_job.id}").json()] == [
        draft["id"]
    ]
    assert admin_client.get("/api/comments?status=sent").json() == []
    actions = [e["action"] for e in admin_client.get("/api/audit").json()]
    assert "comment.created" in actions and "comment.sent" in actions


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://evil.example/video/1",
        "http://www.tiktok.com/@a/video/1",
        "https://tiktok.com.evil.example/x",
    ],
)
def test_video_url_must_point_to_tiktok(admin_client, url):
    assert admin_client.post("/api/comments", json={"video_url": url, "reply": "x"}).status_code == 422


def test_manual_tiktok_link_is_accepted(admin_client):
    r = admin_client.post(
        "/api/comments", json={"video_url": "https://www.tiktok.com/@me/video/1", "reply": "x"}
    )
    assert r.status_code == 200 and r.json()["video_url"] == "https://www.tiktok.com/@me/video/1"
    r = admin_client.patch(f"/api/comments/{r.json()['id']}", json={"video_url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_reminders_api(admin_client):
    due = (utcnow() + timedelta(hours=1)).isoformat()
    draft = admin_client.post("/api/comments", json={"reply": "x"}).json()
    r = admin_client.post(
        "/api/reminders", json={"title": "Ответить", "due_at": due, "comment_draft_id": draft["id"]}
    )
    assert r.status_code == 200, r.text
    reminder = r.json()
    assert reminder["link"] == f"/comments?open={draft['id']}"

    assert len(admin_client.get("/api/reminders").json()) == 1
    admin_client.post(f"/api/reminders/{reminder['id']}/done")
    assert admin_client.get("/api/reminders").json() == []
    assert len(admin_client.get("/api/reminders?state=done").json()) == 1
    admin_client.post(f"/api/reminders/{reminder['id']}/reopen")
    assert len(admin_client.get("/api/reminders").json()) == 1


@pytest.mark.parametrize(
    "link", ["https://evil.example", "//evil.example/x", "javascript:alert(1)", "/\\evil.example"]
)
def test_reminder_link_must_stay_inside_panel(admin_client, link):
    due = (utcnow() + timedelta(hours=1)).isoformat()
    assert (
        admin_client.post("/api/reminders", json={"title": "x", "due_at": due, "link": link}).status_code
        == 422
    )


def test_reminder_requires_timezone(admin_client):
    r = admin_client.post("/api/reminders", json={"title": "x", "due_at": "2026-11-01T10:00:00"})
    assert r.status_code == 422


def test_due_reminder_is_notified_once_and_again_after_reschedule(db, admin, clock):
    reminder = Reminder(
        title="Ответить на комментарии", note="Видео про осень", due_at=clock.now, link="/comments"
    )
    done = Reminder(title="Уже сделано", due_at=clock.now, done_at=clock.now)
    later = Reminder(title="Завтра", due_at=clock.now + timedelta(days=1))
    db.add_all([reminder, done, later])
    db.commit()

    messages = notifications.collect(db, clock.now)
    assert [(e, m.title, m.url) for e, m in messages] == [
        ("reminder", "Напоминание: Ответить на комментарии", "/comments")
    ]
    assert notifications.collect(db, clock.now) == []

    reminder.due_at = clock.now + timedelta(minutes=5)
    reminder.notified_at = None
    db.commit()
    assert notifications.collect(db, clock.now + timedelta(minutes=5))[0][1].title.endswith("комментарии")


def test_viewer_cannot_create_drafts_or_reminders(client, db):
    from app.models import Role, User
    from app.security import hash_password

    db.add(User(username="viewer", password_hash=hash_password("viewer-password"), role=Role.VIEWER))
    db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert client.get("/api/comments").status_code == 200
    assert client.post("/api/comments", json={"reply": "x"}).status_code == 403
    due = (utcnow() + timedelta(hours=1)).isoformat()
    assert client.post("/api/reminders", json={"title": "x", "due_at": due}).status_code == 403
