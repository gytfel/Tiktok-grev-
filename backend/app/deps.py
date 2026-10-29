from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.tiktok import TikTokClient


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        request.session.clear()
        raise HTTPException(401, "Требуется вход")
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


def _enqueue_account_checks(account_ids: list[int]) -> None:
    from app.worker import check_account_task

    for account_id in account_ids:
        check_account_task.delay(account_id)


def get_account_check_enqueuer() -> Callable[[list[int]], None]:
    return _enqueue_account_checks
