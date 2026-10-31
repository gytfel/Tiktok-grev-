import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import audit
from app.accounts_service import ConnectError, connect_account
from app.config import get_settings
from app.db import get_db
from app.deps import get_tiktok, require_writer
from app.models import User, utcnow
from app.tiktok import TikTokClient, TikTokError, describe

router = APIRouter(prefix="/api/oauth/tiktok", tags=["oauth"])

STATE_TTL_S = 15 * 60
MAX_PENDING_STATES = 20


def _back_to_accounts(**params: str) -> RedirectResponse:
    return RedirectResponse(f"/accounts?{urlencode(params)}", status_code=303)


@router.get("/start")
def start(
    request: Request,
    account_id: int | None = None,
    tt: TikTokClient = Depends(get_tiktok),
    _: User = Depends(require_writer),
):
    settings = get_settings()
    if not settings.tiktok_configured:
        raise HTTPException(400, "Не заданы TIKTOK_CLIENT_KEY и TIKTOK_CLIENT_SECRET")
    state = secrets.token_urlsafe(24)
    now = time.time()
    pending = {
        k: v for k, v in request.session.get("oauth_states", {}).items() if now - v["ts"] < STATE_TTL_S
    }
    pending[state] = {"account_id": account_id, "ts": now}
    request.session["oauth_states"] = dict(
        sorted(pending.items(), key=lambda kv: kv[1]["ts"])[-MAX_PENDING_STATES:]
    )
    url = tt.authorize_url(settings.oauth_redirect_uri, state, settings.oauth_scopes)
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
def callback(
    request: Request,
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
    tt: TikTokClient = Depends(get_tiktok),
    user: User = Depends(require_writer),
):
    pending = request.session.get("oauth_states", {})
    entry = pending.pop(state, None)
    request.session["oauth_states"] = pending
    if entry is None or time.time() - entry["ts"] > STATE_TTL_S:
        return _back_to_accounts(oauth_error="Сессия подключения устарела, начните заново")
    if error or not code:
        return _back_to_accounts(oauth_error=error_description or error or "Авторизация отменена")
    try:
        account = connect_account(db, tt, code, entry["account_id"], utcnow())
    except ConnectError as exc:
        db.rollback()
        return _back_to_accounts(oauth_error=str(exc))
    except TikTokError as exc:
        db.rollback()
        return _back_to_accounts(oauth_error=describe(exc.code, exc.message or exc.code))
    audit.record(db, user, "account.connected", "account", account.id, {"open_id": account.open_id})
    db.commit()
    return _back_to_accounts(connected=str(account.id))
