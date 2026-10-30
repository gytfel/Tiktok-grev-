import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, twofactor
from app.config import get_settings
from app.db import get_db
from app.deps import require_admin, session_user
from app.models import Role, User, utcnow
from app.schemas import LoginIn, UserOut
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])

PENDING_2FA_TTL_S = 5 * 60
# Хеш для проверки пароля несуществующего пользователя: время ответа не выдаёт, есть ли логин.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginOut(BaseModel):
    two_factor_required: bool
    user: UserOut | None = None


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


class DisableTwoFactorIn(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=10)


def _start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["sv"] = user.session_version


def _bump_sessions(request: Request, user: User) -> None:
    """Завершает все остальные сессии пользователя, текущую оставляет."""
    user.session_version += 1
    request.session["sv"] = user.session_version


@router.post("/auth/login", response_model=LoginOut)
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)) -> LoginOut:
    user = db.execute(select(User).where(User.username == data.username)).scalar_one_or_none()
    if not verify_password(user.password_hash if user else _DUMMY_HASH, data.password) or user is None:
        raise HTTPException(401, "Неверный логин или пароль")
    request.session.clear()
    if user.totp_enabled:
        request.session["pending_2fa"] = {"user_id": user.id, "ts": time.time()}
        return LoginOut(two_factor_required=True)
    _start_session(request, user)
    audit.record(db, user, "user.login", "user", user.id)
    db.commit()
    return LoginOut(two_factor_required=False, user=UserOut.model_validate(user))


@router.post("/auth/login/2fa", response_model=LoginOut)
def login_second_factor(data: CodeIn, request: Request, db: Session = Depends(get_db)) -> LoginOut:
    pending = request.session.get("pending_2fa")
    if not pending or time.time() - pending.get("ts", 0) > PENDING_2FA_TTL_S:
        request.session.clear()
        raise HTTPException(401, "Время на ввод кода истекло — войдите заново")
    user = db.get(User, pending["user_id"])
    if user is None or not user.totp_enabled:
        request.session.clear()
        raise HTTPException(401, "Войдите заново")
    try:
        twofactor.verify(user, data.code, utcnow())
    except twofactor.TwoFactorError as exc:
        db.commit()
        raise HTTPException(401, str(exc)) from None
    _start_session(request, user)
    audit.record(db, user, "user.login", "user", user.id, {"2fa": True})
    db.commit()
    return LoginOut(two_factor_required=False, user=UserOut.model_validate(user))


@router.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(session_user)) -> User:
    return user


@router.post("/auth/password")
def change_password(
    data: PasswordChangeIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(session_user),
) -> dict:
    if not verify_password(user.password_hash, data.current_password):
        raise HTTPException(403, "Текущий пароль указан неверно")
    user.password_hash = hash_password(data.new_password)
    _bump_sessions(request, user)
    audit.record(db, user, "user.password_changed", "user", user.id)
    db.commit()
    return {"ok": True}


@router.post("/auth/2fa/setup")
def setup_two_factor(db: Session = Depends(get_db), user: User = Depends(session_user)) -> dict:
    if user.totp_enabled:
        raise HTTPException(409, "Двухфакторный вход уже включён")
    result = twofactor.start_setup(user)
    db.commit()
    return result


@router.post("/auth/2fa/enable", response_model=UserOut)
def enable_two_factor(
    data: CodeIn, request: Request, db: Session = Depends(get_db), user: User = Depends(session_user)
) -> User:
    if user.totp_enabled:
        raise HTTPException(409, "Двухфакторный вход уже включён")
    try:
        twofactor.verify(user, data.code, utcnow())
    except twofactor.TwoFactorError as exc:
        db.commit()
        raise HTTPException(422, str(exc)) from None
    user.totp_enabled = True
    _bump_sessions(request, user)
    audit.record(db, user, "user.2fa_enabled", "user", user.id)
    db.commit()
    return user


@router.post("/auth/2fa/disable", response_model=UserOut)
def disable_two_factor(
    data: DisableTwoFactorIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(session_user),
) -> User:
    if not user.totp_enabled:
        raise HTTPException(409, "Двухфакторный вход не включён")
    if not verify_password(user.password_hash, data.password):
        raise HTTPException(403, "Пароль указан неверно")
    try:
        twofactor.verify(user, data.code, utcnow())
    except twofactor.TwoFactorError as exc:
        db.commit()
        raise HTTPException(422, str(exc)) from None
    twofactor.disable(user)
    _bump_sessions(request, user)
    audit.record(db, user, "user.2fa_disabled", "user", user.id)
    db.commit()
    return user


@router.get("/meta")
def meta(_: User = Depends(session_user)) -> dict:
    s = get_settings()
    return {
        "tiktok_configured": s.tiktok_configured,
        "redirect_uri": s.oauth_redirect_uri,
        "emulator": s.tiktok_emulator,
        "max_upload_bytes": s.max_upload_bytes,
        "require_2fa": s.require_2fa,
        "ai_enabled": s.ai_configured,
    }


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=200)
    role: str = Role.OPERATOR


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut)
def create_user(
    data: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> User:
    if data.role not in Role.ALL:
        raise HTTPException(422, "Неизвестная роль")
    if db.execute(select(User).where(User.username == data.username)).scalar_one_or_none():
        raise HTTPException(409, "Такой пользователь уже есть")
    user = User(username=data.username, password_hash=hash_password(data.password), role=data.role)
    db.add(user)
    db.flush()
    audit.record(db, admin, "user.created", "user", user.id, {"username": user.username, "role": user.role})
    db.commit()
    return user


@router.post("/users/{user_id}/reset-2fa", response_model=UserOut)
def reset_user_two_factor(
    user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> User:
    """Для сотрудника, потерявшего телефон: 2FA отключается, все его сессии завершаются."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(409, "Свой двухфакторный вход отключите в разделе «Безопасность»")
    twofactor.disable(user)
    user.session_version += 1
    audit.record(db, admin, "user.2fa_reset", "user", user.id, {"username": user.username})
    db.commit()
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(409, "Нельзя удалить самого себя")
    audit.record(db, admin, "user.deleted", "user", user.id, {"username": user.username})
    db.delete(user)
    db.commit()
    return {"ok": True}
