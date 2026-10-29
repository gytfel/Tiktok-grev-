from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db
from app.deps import current_user, require_admin
from app.models import Role, User
from app.schemas import LoginIn, UserOut
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=UserOut)
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)) -> User:
    user = db.execute(select(User).where(User.username == data.username)).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, data.password):
        raise HTTPException(401, "Неверный логин или пароль")
    request.session.clear()
    request.session["user_id"] = user.id
    audit.record(db, user, "user.login", "user", user.id)
    db.commit()
    return user


@router.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.get("/meta")
def meta(_: User = Depends(current_user)) -> dict:
    s = get_settings()
    return {
        "tiktok_configured": s.tiktok_configured,
        "redirect_uri": s.oauth_redirect_uri,
        "emulator": s.tiktok_emulator,
        "max_upload_bytes": s.max_upload_bytes,
    }


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=200)
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
