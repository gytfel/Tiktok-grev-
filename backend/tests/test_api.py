import httpx
from sqlalchemy import select

from app.models import Account, AccountStatus, AuditEvent, Role, User
from app.security import hash_password


def oauth_connect(client, fake, username, account_id=None):
    """Проходит OAuth так же, как браузер: start → страница TikTok → callback."""
    params = {"account_id": account_id} if account_id else {}
    r = client.get("/api/oauth/tiktok/start", params=params, follow_redirects=False)
    assert r.status_code == 303, r.text
    authorize = httpx.URL(r.headers["location"])
    assert authorize.host == "tiktok.test"
    state = authorize.params["state"]
    code = fake.issue_code(f"open-{username}")
    return client.get(
        "/api/oauth/tiktok/callback",
        params={"code": code, "state": state, "scopes": "user.info.basic,video.publish"},
        follow_redirects=False,
    )


def test_requires_login(client):
    assert client.get("/api/accounts").status_code == 401
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401


def test_viewer_is_read_only(client, db):
    db.add(User(username="viewer", password_hash=hash_password("viewer-pass"), role=Role.VIEWER))
    db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-pass"})
    assert client.get("/api/accounts").status_code == 200
    assert client.post("/api/accounts", json={"label": "x"}).status_code == 403


def test_oauth_connect_creates_account(admin_client, fake):
    r = oauth_connect(admin_client, fake, "alpha")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/accounts?connected=")
    accounts = admin_client.get("/api/accounts").json()
    assert len(accounts) == 1
    a = accounts[0]
    assert a["connection_status"] == AccountStatus.CONNECTED
    assert a["username"] == "alpha"
    assert a["creator_info"]["privacy_level_options"]
    assert "token" not in str(a).lower()


def test_oauth_binds_to_existing_record_and_rejects_other_account(admin_client, fake, db):
    record = admin_client.post("/api/accounts", json={"label": "Аккаунт 1"}).json()
    r = oauth_connect(admin_client, fake, "alpha", record["id"])
    assert r.headers["location"] == f"/accounts?connected={record['id']}"

    # Повторное подключение этой же записи под другим TikTok-аккаунтом — ошибка.
    r = oauth_connect(admin_client, fake, "bravo", record["id"])
    assert "oauth_error" in r.headers["location"]
    assert db.scalar(select(Account).where(Account.open_id == "open-bravo")) is None

    # Тот же TikTok-аккаунт нельзя привязать ко второй записи.
    other = admin_client.post("/api/accounts", json={"label": "Аккаунт 2"}).json()
    r = oauth_connect(admin_client, fake, "alpha", other["id"])
    assert "oauth_error" in r.headers["location"]


def test_oauth_rejects_unknown_state(admin_client, fake):
    r = admin_client.get(
        "/api/oauth/tiktok/callback",
        params={"code": fake.issue_code("open-alpha"), "state": "forged"},
        follow_redirects=False,
    )
    assert "oauth_error" in r.headers["location"]
    assert admin_client.get("/api/accounts").json() == []


def test_csv_import_and_groups(admin_client):
    csv_text = "label;groups;notes\nacc-1;Россия|Основные;первый\nacc-2;Россия;\nacc-1;;дубль\n;;\n"
    r = admin_client.post(
        "/api/accounts/import", files={"file": ("accounts.csv", csv_text.encode("cp1251"), "text/csv")}
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and r.json()["skipped"] == 1 and len(r.json()["errors"]) == 1
    groups = {g["name"]: g["id"] for g in admin_client.get("/api/groups").json()}
    assert set(groups) == {"Россия", "Основные"}
    accounts = admin_client.get("/api/accounts").json()
    assert {g["name"] for g in accounts[0]["groups"]} == {"Россия", "Основные"}

    ids = [a["id"] for a in accounts]
    r = admin_client.post(
        "/api/accounts/bulk",
        json={"account_ids": ids, "action": "remove_from_group", "group_id": groups["Россия"]},
    )
    assert r.status_code == 200
    assert all(
        "Россия" not in {g["name"] for g in a["groups"]} for a in admin_client.get("/api/accounts").json()
    )


def test_bulk_check_is_enqueued(admin_client, enqueued):
    a = admin_client.post("/api/accounts", json={"label": "a"}).json()
    admin_client.post("/api/accounts/bulk", json={"account_ids": [a["id"]], "action": "check"})
    assert enqueued == [a["id"]]


def test_media_upload_validation(admin_client, sample_video):
    r = admin_client.post("/api/media", files={"file": ("clip.mp4", sample_video.read_bytes(), "video/mp4")})
    assert r.status_code == 200, r.text
    media = r.json()
    assert media["validation_status"] == "valid", media["validation_issues"]
    assert media["width"] == 720 and media["height"] == 1280
    assert 1900 <= media["duration_ms"] <= 2100
    assert admin_client.get(f"/api/media/{media['id']}/file").content == sample_video.read_bytes()

    r = admin_client.post("/api/media", files={"file": ("junk.mp4", b"not a video", "video/mp4")})
    assert r.json()["validation_status"] == "invalid"
    r = admin_client.post("/api/media", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 422


def test_campaign_flow_over_http(admin_client, fake, sample_video, db):
    for name in ("alpha", "bravo"):
        oauth_connect(admin_client, fake, name)
    pending = admin_client.post("/api/accounts", json={"label": "не подключён"}).json()
    media = admin_client.post(
        "/api/media", files={"file": ("clip.mp4", sample_video.read_bytes(), "video/mp4")}
    ).json()
    account_ids = [a["id"] for a in admin_client.get("/api/accounts").json()]
    draft = {
        "media_asset_id": media["id"],
        "title": "Запуск",
        "default_caption": "Новое видео",
        "account_ids": account_ids,
        "settings": {"privacy_level": "SELF_ONLY"},
    }
    preview = admin_client.post("/api/campaigns/preview", json=draft).json()
    assert preview["ready_count"] == 2 and preview["blocked_count"] == 1
    blocked = next(c for c in preview["accounts"] if not c["ok"])
    assert blocked["account_id"] == pending["id"]

    r = admin_client.post("/api/campaigns", json={**draft, "snapshot_hash": "stale", "consent": True})
    assert r.status_code == 409
    assert r.json()["preview"]["snapshot_hash"] == preview["snapshot_hash"]

    r = admin_client.post(
        "/api/campaigns", json={**draft, "snapshot_hash": preview["snapshot_hash"], "consent": True}
    )
    assert r.status_code == 200, r.text
    campaign = r.json()
    assert campaign["total"] == 2 and campaign["counts"] == {"queued": 2}
    assert campaign["confirmed_by"] == "admin"

    detail = admin_client.get(f"/api/campaigns/{campaign['id']}").json()
    assert {j["account_username"] for j in detail["jobs"]} == {"alpha", "bravo"}
    job_id = detail["jobs"][0]["id"]
    assert admin_client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    assert admin_client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert admin_client.post(f"/api/campaigns/{campaign['id']}/cancel").json()["cancelled"] == 1
    assert admin_client.get("/api/campaigns").json()[0]["status"] == "cancelled"

    # Видео, использованное в кампании, удалить нельзя.
    assert admin_client.delete(f"/api/media/{media['id']}").status_code == 409

    actions = {e["action"] for e in admin_client.get("/api/audit").json()}
    assert {
        "account.connected",
        "media.uploaded",
        "campaign.confirmed",
        "job.cancelled",
        "campaign.cancelled",
    } <= actions
    assert (
        db.scalar(select(AuditEvent).where(AuditEvent.action == "campaign.confirmed")).actor_name == "admin"
    )


def test_account_with_pending_jobs_cannot_be_deleted(admin_client, fake, sample_video):
    oauth_connect(admin_client, fake, "alpha")
    account = admin_client.get("/api/accounts").json()[0]
    media = admin_client.post(
        "/api/media", files={"file": ("clip.mp4", sample_video.read_bytes(), "video/mp4")}
    ).json()
    draft = {
        "media_asset_id": media["id"],
        "title": "x",
        "account_ids": [account["id"]],
        "settings": {"privacy_level": "SELF_ONLY"},
    }
    preview = admin_client.post("/api/campaigns/preview", json=draft).json()
    admin_client.post(
        "/api/campaigns", json={**draft, "snapshot_hash": preview["snapshot_hash"], "consent": True}
    )
    assert admin_client.delete(f"/api/accounts/{account['id']}").status_code == 409


def test_users_admin_only(admin_client):
    r = admin_client.post(
        "/api/users", json={"username": "op", "password": "operator-pass", "role": "operator"}
    )
    assert r.status_code == 200
    assert {u["username"] for u in admin_client.get("/api/users").json()} == {"admin", "op"}
    admin_client.post("/api/auth/logout")
    admin_client.post("/api/auth/login", json={"username": "op", "password": "operator-pass"})
    assert admin_client.get("/api/users").status_code == 403
