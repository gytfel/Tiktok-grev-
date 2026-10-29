import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_tmp = Path(tempfile.mkdtemp(prefix="tiktok-queue-tests-"))
os.environ.update(
    {
        "SECRET_KEY": "test-secret",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}"),
        "MEDIA_DIR": str(_tmp / "media"),
        "PUBLIC_BASE_URL": "http://testserver",
        "TIKTOK_CLIENT_KEY": "test-client-key",
        "TIKTOK_CLIENT_SECRET": "test-client-secret",
        "TIKTOK_API_BASE": "http://tiktok.test",
        "TIKTOK_AUTH_URL": "http://tiktok.test/v2/auth/authorize/",
        "ADMIN_USERNAME": "",
        "ADMIN_PASSWORD": "",
        "UPLOAD_CHUNK_SIZE": str(10 * 1024 * 1024),
    }
)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app import publishing  # noqa: E402
from app.accounts_service import save_tokens  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.deps import get_account_check_enqueuer, get_tiktok  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountStatus,
    Base,
    MediaAsset,
    Role,
    User,
)
from app.schemas import CampaignConfirm, PostSettings  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.tiktok import TikTokClient, TokenSet  # noqa: E402
from fake_tiktok import FakeTikTok  # noqa: E402

engine = get_engine()
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(publishing.time, "sleep", lambda _s: None)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake(clock):
    f = FakeTikTok("http://tiktok.test", clock=lambda: clock.now.timestamp())
    for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"):
        f.add_creator(name, name.title())
    return f


@pytest.fixture
def tt(fake):
    return TikTokClient.from_settings(
        get_settings(), http=httpx.Client(transport=httpx.MockTransport(fake.handle))
    )


@pytest.fixture
def admin(db):
    user = User(username="admin", password_hash=hash_password("admin-password"), role=Role.ADMIN)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def enqueued():
    return []


@pytest.fixture
def client(tt, enqueued):
    from app.main import app

    app.dependency_overrides[get_tiktok] = lambda: tt
    app.dependency_overrides[get_account_check_enqueuer] = lambda: enqueued.extend
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(client, admin):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert r.status_code == 200, r.text
    return client


def connect(db, fake: FakeTikTok, tt: TikTokClient, username: str, clock: Clock) -> Account:
    """Создаёт подключённый аккаунт так же, как это делает OAuth callback."""
    open_id = f"open-{username}"
    if open_id not in fake.creators:
        fake.add_creator(username)
    ts: TokenSet = tt.exchange_code(fake.issue_code(open_id), get_settings().oauth_redirect_uri)
    account = Account(label=username, open_id=open_id, connection_status=AccountStatus.CONNECTED)
    db.add(account)
    save_tokens(db, account, ts, clock.now)
    db.commit()
    return account


def make_media(db, size: int = 1_000_000, duration_ms: int = 10_000, name: str = "clip.mp4") -> MediaAsset:
    settings = get_settings()
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    key = f"{os.urandom(8).hex()}.mp4"
    data = os.urandom(size)
    (Path(settings.media_dir) / key).write_bytes(data)
    import hashlib

    media = MediaAsset(
        original_filename=name,
        storage_key=key,
        content_type="video/mp4",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=size,
        duration_ms=duration_ms,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        validation_status="valid",
        validation_issues=[],
    )
    db.add(media)
    db.commit()
    return media


def confirm(db, user, media, accounts, clock, **overrides):
    """Проходит предпросмотр и подтверждение кампании."""
    draft = {
        "media_asset_id": media.id,
        "title": "Тестовая кампания",
        "default_caption": "Подпись #test",
        "account_ids": [a.id for a in accounts],
        "settings": PostSettings(privacy_level="PUBLIC_TO_EVERYONE"),
        "consent": True,
        "snapshot_hash": "",
        **overrides,
    }
    data = CampaignConfirm(**draft)
    preview, _ = publishing.build_preview(db, data)
    data.snapshot_hash = preview.snapshot_hash
    return publishing.confirm_campaign(db, data, user, clock.now)


@pytest.fixture(scope="session")
def sample_video() -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    path = _tmp / "sample.mp4"
    if not path.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=720x1280:rate=30:duration=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
    return path
