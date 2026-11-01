class TikTokError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        log_id: str | None = None,
        http_status: int | None = None,
        transient: bool | None = None,
    ):
        self.code = code
        self.message = message
        self.log_id = log_id
        self.http_status = http_status
        if transient is None:
            transient = code in TRANSIENT_CODES or (http_status is not None and http_status >= 500)
        self.transient = transient
        super().__init__(f"{code}: {message}" if message else code)

    @property
    def is_auth_problem(self) -> bool:
        return self.code in AUTH_CODES

    def details(self) -> str:
        parts = [self.code]
        if self.message:
            parts.append(self.message)
        if self.log_id:
            parts.append(f"log_id={self.log_id}")
        return " | ".join(parts)


TRANSIENT_CODES = {"rate_limit_exceeded", "internal_error", "internal", "network_error"}
AUTH_CODES = {
    "access_token_invalid",
    "scope_not_authorized",
    "scope_permission_missed",
    "auth_removed",
    "invalid_grant",
    "invalid_token",
}

# Коды ошибок API и значения fail_reason из status/fetch.
MESSAGES = {
    "access_token_invalid": "Токен доступа недействителен — подключите аккаунт заново",
    "invalid_grant": "Авторизация истекла или отозвана — подключите аккаунт заново",
    "invalid_token": "Токен недействителен — подключите аккаунт заново",
    "scope_not_authorized": "Аккаунт не выдал разрешение video.publish — подключите заново",
    "scope_permission_missed": "Аккаунт не выдал разрешение video.publish — подключите заново",
    "auth_removed": "Владелец аккаунта отозвал доступ приложения",
    "rate_limit_exceeded": "Превышен лимит запросов TikTok API",
    "spam_risk_too_many_posts": "TikTok: достигнут дневной лимит публикаций для этого аккаунта",
    "spam_risk_user_banned_from_posting": "TikTok: аккаунту запрещено публиковать",
    "spam_risk_text": "TikTok отклонил подпись",
    "spam_risk": "TikTok отклонил публикацию (spam risk)",
    "reached_active_user_cap": (
        "TikTok: исчерпан лимит активных авторов приложения за 24 часа "
        "(у приложения без аудита — 5 аккаунтов)"
    ),
    # Ограничение касается и видимости видео, и настройки самого аккаунта TikTok.
    "unaudited_client_can_only_post_to_private_accounts": (
        "Приложение ещё не прошло аудит TikTok: публиковать можно только в закрытый аккаунт "
        "и с видимостью «Только я». Сделайте аккаунт закрытым в приложении TikTok "
        "(Настройки и конфиденциальность → Конфиденциальность → Закрытый аккаунт) и повторите"
    ),
    "privacy_level_option_mismatch": "Выбранная видимость недоступна для этого аккаунта",
    "url_ownership_unverified": "Домен для PULL_FROM_URL не подтверждён",
    "invalid_param": "TikTok отклонил параметры запроса",
    "internal_error": "Внутренняя ошибка TikTok",
    "internal": "Внутренняя ошибка TikTok",
    "network_error": "Сетевая ошибка при обращении к TikTok",
    "invalid_response": "Неожиданный ответ TikTok",
    "file_format_check_failed": "TikTok отклонил формат файла",
    "duration_check_failed": "TikTok отклонил длительность видео",
    "frame_rate_check_failed": "TikTok отклонил частоту кадров",
    "picture_size_check_failed": "TikTok отклонил разрешение видео",
    "video_pull_failed": "TikTok не смог загрузить видео",
    "publish_cancelled": "Публикация отменена",
    "upload_incomplete": "Загрузка файла не завершилась — публикация не состоялась",
}


def describe(code: str | None, fallback: str = "") -> str:
    if not code:
        return fallback
    return MESSAGES.get(code, fallback or code)
