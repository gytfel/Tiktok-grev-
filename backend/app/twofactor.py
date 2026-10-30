"""Двухфакторный вход по одноразовым кодам (TOTP, RFC 6238): Google Authenticator, Яндекс Ключ и др."""

import base64
import hmac
import io
import time
from datetime import datetime, timedelta

import pyotp
import qrcode
import qrcode.image.svg

from app.models import User
from app.security import decrypt_secret, encrypt_secret

ISSUER = "Очередь публикаций"
MAX_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)
# Допускаем расхождение часов телефона и сервера на один шаг (30 секунд) в обе стороны.
VALID_WINDOW = 1


class TwoFactorError(Exception):
    pass


def start_setup(user: User) -> dict:
    """Создаёт новый секрет (ещё не включённый) и данные для приложения-аутентификатора."""
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt_secret(secret)
    user.totp_last_step = None
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=ISSUER)
    svg = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, border=2).save(svg)
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "qr_data_url": "data:image/svg+xml;base64," + base64.b64encode(svg.getvalue()).decode(),
    }


def verify(user: User, code: str, now: datetime, clock: float | None = None) -> None:
    """Проверяет код. Ошибки считаются на сервере; после нескольких неудач проверка блокируется."""
    if not user.totp_secret_enc:
        raise TwoFactorError("Двухфакторный вход не настроен")
    if user.totp_locked_until and user.totp_locked_until > now:
        raise TwoFactorError("Слишком много неверных кодов. Попробуйте через 15 минут.")
    code = code.replace(" ", "").strip()
    totp = pyotp.TOTP(decrypt_secret(user.totp_secret_enc))
    current_step = int((clock if clock is not None else time.time()) // totp.interval)
    for step in range(current_step - VALID_WINDOW, current_step + VALID_WINDOW + 1):
        expected = totp.at(step * totp.interval)
        if hmac.compare_digest(expected, code) and (
            user.totp_last_step is None or step > user.totp_last_step
        ):
            user.totp_last_step = step
            user.totp_failures = 0
            user.totp_locked_until = None
            return
    user.totp_failures = (user.totp_failures or 0) + 1
    if user.totp_failures >= MAX_FAILURES:
        user.totp_failures = 0
        user.totp_locked_until = now + LOCK_DURATION
        raise TwoFactorError("Слишком много неверных кодов. Попробуйте через 15 минут.")
    raise TwoFactorError("Неверный код")


def disable(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.totp_last_step = None
    user.totp_failures = 0
    user.totp_locked_until = None
