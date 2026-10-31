import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Role, User
from app.routers import (
    accounts,
    analytics,
    assistant,
    auth,
    calendar,
    campaigns,
    comments,
    media,
    oauth,
    push,
)
from app.security import hash_password

log = logging.getLogger(__name__)
mimetypes.add_type("application/manifest+json", ".webmanifest")

# Файлы, которые браузер должен перепроверять при каждом запуске, иначе PWA не увидит обновление.
NO_CACHE = {"index.html", "sw.js", "manifest.webmanifest"}


def bootstrap_admin() -> None:
    """Создаёт первого администратора из ADMIN_USERNAME/ADMIN_PASSWORD, если пользователей нет."""
    settings = get_settings()
    if not (settings.admin_username and settings.admin_password):
        return
    with get_sessionmaker()() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        db.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                role=Role.ADMIN,
            )
        )
        db.commit()
        log.info("Создан администратор %s", settings.admin_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_admin()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="TikTok Publishing Queue", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="tt_session",
        max_age=7 * 24 * 3600,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    for module in (auth, accounts, oauth, media, campaigns, push, assistant, comments, calendar, analytics):
        app.include_router(module.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    dist = Path(settings.frontend_dist) if settings.frontend_dist else None
    if dist and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (dist / path).resolve()
            if not (path and candidate.is_file() and candidate.is_relative_to(dist.resolve())):
                candidate = dist / "index.html"
            headers = {"Cache-Control": "no-cache"} if candidate.name in NO_CACHE else None
            return FileResponse(candidate, headers=headers)

    return app


app = create_app()
