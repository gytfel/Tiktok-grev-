from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountStatus, AccountToken
from app.security import decrypt_secret, encrypt_secret
from app.tiktok import TikTokClient, TikTokError, TokenSet, describe

REFRESH_MARGIN = timedelta(minutes=5)
CREATOR_INFO_FIELDS = (
    "privacy_level_options",
    "comment_disabled",
    "duet_disabled",
    "stitch_disabled",
    "max_video_post_duration_sec",
)


class NeedsReauth(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ConnectError(Exception):
    pass


def mark_needs_reauth(account: Account, message: str) -> None:
    account.connection_status = AccountStatus.NEEDS_REAUTH
    account.last_error = message


def save_tokens(db: Session, account: Account, ts: TokenSet, now: datetime) -> None:
    token = db.get(AccountToken, account.id) if account.id else None
    if token is None:
        token = AccountToken(account=account)
        db.add(token)
    token.access_token_enc = encrypt_secret(ts.access_token)
    token.refresh_token_enc = encrypt_secret(ts.refresh_token)
    token.access_expires_at = now + timedelta(seconds=ts.expires_in)
    token.refresh_expires_at = now + timedelta(seconds=ts.refresh_expires_in)
    account.scopes = ts.scope


def ensure_access_token(db: Session, account: Account, tt: TikTokClient, now: datetime) -> str:
    """Возвращает действующий access token, при необходимости обновляя его.

    Строка токена блокируется на время обновления, чтобы два процесса не обновляли
    один refresh token одновременно.
    """
    if account.connection_status != AccountStatus.CONNECTED:
        raise NeedsReauth("not_connected", "Аккаунт не подключён к TikTok")
    token = db.execute(
        select(AccountToken).where(AccountToken.account_id == account.id).with_for_update()
    ).scalar_one_or_none()
    if token is None:
        mark_needs_reauth(account, "Нет сохранённой авторизации")
        db.commit()
        raise NeedsReauth("no_token", "Нет сохранённой авторизации")
    if token.access_expires_at - now > REFRESH_MARGIN:
        return decrypt_secret(token.access_token_enc)
    if token.refresh_expires_at <= now:
        mark_needs_reauth(account, "Срок действия авторизации истёк")
        db.commit()
        raise NeedsReauth("refresh_expired", "Срок действия авторизации истёк")
    try:
        ts = tt.refresh(decrypt_secret(token.refresh_token_enc))
    except TikTokError as exc:
        if exc.transient:
            db.rollback()
            raise
        message = describe(exc.code, "Не удалось обновить токен")
        mark_needs_reauth(account, message)
        db.commit()
        raise NeedsReauth(exc.code, message) from exc
    save_tokens(db, account, ts, now)
    db.commit()
    return ts.access_token


def apply_creator_info(account: Account, info: dict, now: datetime) -> None:
    account.display_name = info.get("creator_nickname") or account.display_name
    account.username = info.get("creator_username") or account.username
    account.avatar_url = info.get("creator_avatar_url") or account.avatar_url
    account.creator_info = {k: info.get(k) for k in CREATOR_INFO_FIELDS}
    account.creator_info_checked_at = now


def check_account(db: Session, account: Account, tt: TikTokClient, now: datetime) -> str | None:
    """Обновляет данные creator_info. Возвращает текст ошибки или None."""
    try:
        token = ensure_access_token(db, account, tt, now)
        info = tt.creator_info(token)
    except NeedsReauth as exc:
        return exc.message
    except TikTokError as exc:
        message = describe(exc.code, exc.message)
        if exc.is_auth_problem:
            mark_needs_reauth(account, message)
        else:
            account.last_error = message
        db.commit()
        return message
    apply_creator_info(account, info, now)
    account.last_error = None
    db.commit()
    return None


def connect_account(
    db: Session, tt: TikTokClient, code: str, target_account_id: int | None, now: datetime
) -> Account:
    """Обменивает код авторизации на токены и привязывает их к записи реестра."""
    ts = tt.exchange_code(code, get_settings().oauth_redirect_uri)
    if "video.publish" not in ts.scope.replace(" ", "").split(","):
        raise ConnectError("Не выдано разрешение video.publish — без него публикация невозможна")

    existing = db.execute(select(Account).where(Account.open_id == ts.open_id)).scalar_one_or_none()
    if target_account_id is not None:
        account = db.get(Account, target_account_id)
        if account is None:
            raise ConnectError("Запись аккаунта не найдена")
        if existing is not None and existing.id != account.id:
            raise ConnectError(f"Этот TikTok-аккаунт уже подключён как «{existing.label}»")
        if account.open_id and account.open_id != ts.open_id:
            raise ConnectError("Выполнен вход в другой TikTok-аккаунт, чем был подключён к этой записи")
    else:
        account = existing
        if account is None:
            account = Account(label=f"TikTok {ts.open_id[:8]}")
            db.add(account)

    account.open_id = ts.open_id
    account.connection_status = AccountStatus.CONNECTED
    account.last_error = None
    save_tokens(db, account, ts, now)
    db.flush()
    try:
        info = tt.creator_info(ts.access_token)
    except TikTokError as exc:
        account.last_error = describe(exc.code, exc.message)
    else:
        apply_creator_info(account, info, now)
        if account.label.startswith("TikTok ") and info.get("creator_username"):
            account.label = info["creator_username"]
    db.commit()
    return account


def disconnect_account(db: Session, account: Account, tt: TikTokClient | None) -> None:
    token = db.get(AccountToken, account.id)
    if token is not None:
        if tt is not None:
            try:
                tt.revoke(decrypt_secret(token.access_token_enc))
            except TikTokError:
                pass
        db.delete(token)
    account.connection_status = AccountStatus.NOT_CONNECTED
    account.creator_info = None
    account.creator_info_checked_at = None
