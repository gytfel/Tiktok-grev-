from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.ai import AIClient
from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.tiktok import TikTokClient


def session_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Вошедший пользователь, даже если ему ещё нужно включить двухфакторный вход."""
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    # После смены пароля или 2FA версия растёт, и выданные раньше сессии перестают действовать.
    if user is None or request.session.get("sv") != user.session_version:
        # Удаляем только признаки входа: незавершённый вход с кодом 2FA должен сохраниться.
        request.session.pop("user_id", None)
        request.session.pop("sv", None)
        raise HTTPException(401, "Требуется вход")
    return user


def current_user(user: User = Depends(session_user)) -> User:
    if get_settings().require_2fa and not user.totp_enabled:
        raise HTTPException(403, "Включите двухфакторный вход в разделе «Безопасность»")
    return user


def require_writer(user: User = Depends(current_user)) -> User:
    if user.role not in (Role.ADMIN, Role.OPERATOR):
        raise HTTPException(403, "Недостаточно прав")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(403, "Только для администратора")
    return user


@lru_cache
def _tiktok() -> TikTokClient:
    return TikTokClient.from_settings(get_settings())


def get_tiktok() -> TikTokClient:
    return _tiktok()


@lru_cache
def _ai() -> AIClient:
    return AIClient.from_settings(get_settings())


def get_ai() -> AIClient | None:
    return _ai() if get_settings().ai_configured else None


def _enqueue_account_checks(account_ids: list[int]) -> None:
    from app.worker import check_account_task

    for account_id in account_ids:
        check_account_task.delay(account_id)


def _enqueue_analytics(account_ids: list[int]) -> None:
    from app.worker import collect_analytics_task

    for account_id in account_ids:
        collect_analytics_task.delay(account_id)


def get_analytics_enqueuer() -> Callable[[list[int]], None]:
    return _enqueue_analytics


def get_account_check_enqueuer() -> Callable[[list[int]], None]:
    return _enqueue_account_checks
