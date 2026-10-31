"""Клиент официального TikTok API: Login Kit (OAuth 2.0) и Content Posting API (Direct Post).

Документация:
  https://developers.tiktok.com/doc/login-kit-web
  https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
  https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.tiktok.errors import TikTokError

# Ограничения FILE_UPLOAD: чанк от 5 МБ до 64 МБ, последний — до 128 МБ, не более 1000 чанков.
# Границы взяты с запасом, чтобы подходить при любом прочтении «МБ».
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1000 * 1000
MAX_CHUNKS = 1000

CONTENT_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    open_id: str
    scope: str
    expires_in: int
    refresh_expires_in: int


def plan_chunks(video_size: int, preferred_chunk: int) -> tuple[int, int]:
    """Возвращает (chunk_size, total_chunk_count) по правилам media transfer guide."""
    if video_size <= 0:
        raise ValueError("video_size must be positive")
    if video_size <= MAX_CHUNK:
        return video_size, 1
    chunk = min(max(preferred_chunk, MIN_CHUNK), MAX_CHUNK)
    while video_size // chunk > MAX_CHUNKS:
        chunk = min(chunk * 2, MAX_CHUNK)
    # Остаток уходит в последний чанк, поэтому количество округляется вниз.
    return chunk, video_size // chunk


def chunk_ranges(video_size: int, chunk_size: int, total: int) -> list[tuple[int, int]]:
    ranges = []
    for i in range(total):
        start = i * chunk_size
        end = video_size - 1 if i == total - 1 else start + chunk_size - 1
        ranges.append((start, end))
    return ranges


class TikTokClient:
    def __init__(
        self,
        client_key: str,
        client_secret: str,
        api_base: str,
        auth_url: str,
        http: httpx.Client | None = None,
    ):
        self.client_key = client_key
        self.client_secret = client_secret
        self.api_base = api_base.rstrip("/")
        self.auth_url = auth_url
        self.http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.Client | None = None) -> "TikTokClient":
        return cls(
            settings.tiktok_client_key,
            settings.tiktok_client_secret,
            settings.tiktok_api_base,
            settings.tiktok_auth_url,
            http=http,
        )

    # --- OAuth ---------------------------------------------------------------------------

    def authorize_url(self, redirect_uri: str, state: str, scopes: str) -> str:
        params = {
            "client_key": self.client_key,
            "scope": scopes,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            # Всегда показывать экран авторизации, чтобы можно было выбрать нужный аккаунт.
            "disable_auto_auth": "1",
        }
        return f"{self.auth_url}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        return self._token_request(
            {"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def revoke(self, access_token: str) -> None:
        body, status = self._send(
            "POST",
            "/v2/oauth/revoke/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "token": access_token,
            },
        )
        self._raise_oauth_error(body, status)

    def _token_request(self, data: dict[str, str]) -> TokenSet:
        payload = {"client_key": self.client_key, "client_secret": self.client_secret, **data}
        body, status = self._send("POST", "/v2/oauth/token/", data=payload)
        self._raise_oauth_error(body, status)
        try:
            return TokenSet(
                access_token=body["access_token"],
                refresh_token=body["refresh_token"],
                open_id=body["open_id"],
                scope=body.get("scope", ""),
                expires_in=int(body["expires_in"]),
                refresh_expires_in=int(body["refresh_expires_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TikTokError("invalid_response", f"token response: {exc}") from exc

    @staticmethod
    def _raise_oauth_error(body: dict[str, Any], status: int) -> None:
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if code and code != "ok":
                raise TikTokError(code, err.get("message", ""), log_id=err.get("log_id"), http_status=status)
        elif err:
            raise TikTokError(
                str(err),
                body.get("error_description", ""),
                log_id=body.get("log_id"),
                http_status=status,
            )
        if status >= 400:
            raise TikTokError(f"http_{status}", http_status=status)

    # --- Content Posting API -------------------------------------------------------------

    def creator_info(self, access_token: str) -> dict[str, Any]:
        return self._api(access_token, "/v2/post/publish/creator_info/query/", {})

    def init_direct_post(
        self,
        access_token: str,
        post_info: dict[str, Any],
        video_size: int,
        chunk_size: int,
        total_chunk_count: int,
    ) -> tuple[str, str]:
        data = self._api(
            access_token,
            "/v2/post/publish/video/init/",
            {
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunk_count,
                },
            },
        )
        try:
            return data["publish_id"], data["upload_url"]
        except KeyError as exc:
            raise TikTokError("invalid_response", f"init response: missing {exc}") from exc

    def upload_chunk(
        self,
        upload_url: str,
        chunk: bytes,
        start: int,
        end: int,
        total: int,
        content_type: str,
    ) -> int:
        try:
            r = self.http.put(
                upload_url,
                content=chunk,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total}",
                },
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
        except httpx.TransportError as exc:
            raise TikTokError("network_error", str(exc), transient=True) from exc
        if r.status_code >= 300:
            raise TikTokError(
                f"upload_http_{r.status_code}",
                r.text[:300],
                http_status=r.status_code,
                transient=r.status_code >= 500 or r.status_code == 429,
            )
        return r.status_code

    def fetch_status(self, access_token: str, publish_id: str) -> dict[str, Any]:
        return self._api(access_token, "/v2/post/publish/status/fetch/", {"publish_id": publish_id})

    # --- Display API: статистика своих видео и аккаунта ---------------------------------

    def user_stats(self, access_token: str) -> dict[str, Any]:
        """Подписчики, лайки и число видео аккаунта (разрешение user.info.stats)."""
        fields = "open_id,follower_count,following_count,likes_count,video_count"
        data = self._api(access_token, f"/v2/user/info/?fields={fields}", None, method="GET")
        return data.get("user") or {}

    def query_videos(self, access_token: str, video_ids: list[str]) -> list[dict[str, Any]]:
        """Показатели публичных видео аккаунта, до 20 за запрос (разрешение video.list)."""
        fields = "id,view_count,like_count,comment_count,share_count"
        data = self._api(
            access_token, f"/v2/video/query/?fields={fields}", {"filters": {"video_ids": video_ids[:20]}}
        )
        return data.get("videos") or []

    # --- транспорт -----------------------------------------------------------------------

    def _api(
        self, access_token: str, path: str, payload: dict[str, Any] | None, method: str = "POST"
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        kwargs: dict[str, Any] = {"headers": headers}
        if method == "POST":
            headers["Content-Type"] = "application/json; charset=UTF-8"
            kwargs["json"] = payload
        body, status = self._send(method, path, **kwargs)
        err = body.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else str(err)
        if (code and code != "ok") or status >= 400:
            raise TikTokError(
                code or f"http_{status}",
                err.get("message", "") if isinstance(err, dict) else "",
                log_id=err.get("log_id") if isinstance(err, dict) else None,
                http_status=status,
            )
        return body.get("data") or {}

    def _send(self, method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
        try:
            r = self.http.request(method, f"{self.api_base}{path}", **kwargs)
        except httpx.TransportError as exc:
            raise TikTokError("network_error", str(exc), transient=True) from exc
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise TikTokError("invalid_response", r.text[:300], http_status=r.status_code)
        return body, r.status_code
