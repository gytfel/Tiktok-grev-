import time

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import Role, User
from app.security import hash_password

PASSWORD = "admin-password"


def code(secret: str, steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + steps * 30)


def enable_2fa(client) -> str:
    setup = client.post("/api/auth/2fa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert setup["qr_data_url"].startswith("data:image/svg+xml;base64,")
    r = client.post("/api/auth/2fa/enable", json={"code": code(setup["secret"])})
    assert r.status_code == 200 and r.json()["totp_enabled"], r.text
    return setup["secret"]


def new_client():
    from app.main import app

    return TestClient(app)


def test_two_step_login(admin_client):
    secret = enable_2fa(admin_client)
    admin_client.post("/api/auth/logout")

    r = admin_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.json() == {"two_factor_required": True, "user": None}
    # Пароль без кода не даёт доступа.
    assert admin_client.get("/api/accounts").status_code == 401
    assert admin_client.post("/api/auth/login/2fa", json={"code": "000000"}).status_code == 401

    r = admin_client.post("/api/auth/login/2fa", json={"code": code(secret, 1)})
    assert r.status_code == 200 and r.json()["user"]["username"] == "admin"
    assert admin_client.get("/api/accounts").status_code == 200


def test_code_cannot_be_reused(admin_client):
    secret = enable_2fa(admin_client)
    used = code(secret, 1)
    admin_client.post("/api/auth/logout")
    admin_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert admin_client.post("/api/auth/login/2fa", json={"code": used}).status_code == 200

    admin_client.post("/api/auth/logout")
    admin_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert admin_client.post("/api/auth/login/2fa", json={"code": used}).status_code == 401


def test_guessing_codes_locks_verification(admin_client):
    secret = enable_2fa(admin_client)
    admin_client.post("/api/auth/logout")
    admin_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    pending_cookie = dict(admin_client.cookies)
    for _ in range(5):
        # Старая cookie с тем же состоянием не сбрасывает счётчик: он хранится на сервере.
        admin_client.cookies.update(pending_cookie)
        assert admin_client.post("/api/auth/login/2fa", json={"code": "123456"}).status_code == 401
    r = admin_client.post("/api/auth/login/2fa", json={"code": code(secret, 1)})
    assert r.status_code == 401 and "15 минут" in r.json()["detail"]


def test_require_2fa_limits_access_until_enabled(admin_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_2fa", True)
    assert admin_client.get("/api/accounts").status_code == 403
    assert admin_client.get("/api/auth/me").status_code == 200
    assert admin_client.get("/api/meta").json()["require_2fa"] is True
    enable_2fa(admin_client)
    assert admin_client.get("/api/accounts").status_code == 200


def test_password_change_ends_other_sessions(admin_client):
    other = new_client()
    other.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert other.get("/api/accounts").status_code == 200

    r = admin_client.post(
        "/api/auth/password", json={"current_password": "wrong", "new_password": "new-password-123"}
    )
    assert r.status_code == 403
    r = admin_client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "new-password-123"}
    )
    assert r.status_code == 200
    assert admin_client.get("/api/accounts").status_code == 200
    assert other.get("/api/accounts").status_code == 401
    assert other.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 401
    assert (
        other.post("/api/auth/login", json={"username": "admin", "password": "new-password-123"}).status_code
        == 200
    )


def test_disable_requires_password_and_code(admin_client):
    secret = enable_2fa(admin_client)
    r = admin_client.post("/api/auth/2fa/disable", json={"password": "wrong", "code": code(secret, 1)})
    assert r.status_code == 403
    r = admin_client.post("/api/auth/2fa/disable", json={"password": PASSWORD, "code": "000000"})
    assert r.status_code == 422
    r = admin_client.post("/api/auth/2fa/disable", json={"password": PASSWORD, "code": code(secret, 1)})
    assert r.status_code == 200 and not r.json()["totp_enabled"]


def test_admin_can_reset_operator_2fa(admin_client, db):
    operator = User(username="op", password_hash=hash_password("operator-password"), role=Role.OPERATOR)
    db.add(operator)
    db.commit()
    op = new_client()
    op.post("/api/auth/login", json={"username": "op", "password": "operator-password"})
    enable_2fa(op)

    r = admin_client.post(f"/api/users/{operator.id}/reset-2fa")
    assert r.status_code == 200 and not r.json()["totp_enabled"]
    assert op.get("/api/accounts").status_code == 401
    r = op.post("/api/auth/login", json={"username": "op", "password": "operator-password"})
    assert r.json()["two_factor_required"] is False


def test_unknown_user_and_wrong_password_look_the_same(client, admin):
    a = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    b = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


@pytest.mark.parametrize("path", ["/api/auth/2fa/setup", "/api/auth/password"])
def test_security_endpoints_require_login(client, path):
    assert client.post(path, json={}).status_code in (401, 422)
