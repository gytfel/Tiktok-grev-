"""Эмулятор TikTok Login Kit и Content Posting API для разработки и тестов.

Повторяет форму запросов и ответов из документации TikTok for Developers, чтобы панель
можно было проверить без одобренного приложения. С настоящим TikTok не связан.
"""

import html
import itertools
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1000 * 1000
ALL_PRIVACY = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]


@dataclass
class FakeCreator:
    open_id: str
    username: str
    nickname: str
    privacy_level_options: list[str] = field(default_factory=lambda: list(ALL_PRIVACY))
    comment_disabled: bool = False
    duet_disabled: bool = False
    stitch_disabled: bool = False
    max_video_post_duration_sec: int = 600


@dataclass
class FakePublish:
    publish_id: str
    open_id: str
    post_info: dict[str, Any]
    video_size: int
    chunk_size: int
    total_chunks: int
    ranges: set[tuple[int, int]] = field(default_factory=set)
    completed_at: float | None = None
    fail_reason: str | None = None
    post_id: int | None = None

    @property
    def uploaded_bytes(self) -> int:
        return sum(end - start + 1 for start, end in self.ranges)


class DropResponse(Exception):
    """Сервер выполнил запрос, но клиент не получил ответ (обрыв соединения)."""


class FakeTikTok:
    def __init__(
        self,
        api_base: str = "http://tiktok.test",
        *,
        client_key: str = "test-client-key",
        client_secret: str = "test-client-secret",
        unaudited: bool = False,
        processing_delay_s: float = 0.0,
        access_ttl_s: int = 86400,
        clock: Callable[[], float] = time.time,
    ):
        self.api_base = api_base.rstrip("/")
        self.client_key = client_key
        self.client_secret = client_secret
        self.unaudited = unaudited
        self.processing_delay_s = processing_delay_s
        self.access_ttl_s = access_ttl_s
        self.clock = clock
        self.creators: dict[str, FakeCreator] = {}
        self.codes: dict[str, str] = {}
        self.access_tokens: dict[str, tuple[str, float]] = {}
        self.refresh_tokens: dict[str, str] = {}
        self.publishes: dict[str, FakePublish] = {}
        self.posting_creators: dict[str, float] = {}
        self.calls: list[tuple[str, str]] = []
        # Внедрение сбоев: путь -> очередь ответов (http_status, error_code) или "drop".
        self.faults: dict[str, list[Any]] = {}
        # Сбой на загрузке чанка с указанным номером (0 — первый): "drop_after" или http-статус.
        self.upload_faults: dict[int, list[Any]] = {}
        self.fail_reason_for_next_publish: str | None = None
        self._ids = itertools.count(1)

    # --- подготовка данных ---------------------------------------------------------------

    def add_creator(self, username: str, nickname: str | None = None, **kwargs: Any) -> FakeCreator:
        open_id = f"open-{username}"
        creator = FakeCreator(open_id=open_id, username=username, nickname=nickname or username, **kwargs)
        self.creators[open_id] = creator
        return creator

    def issue_code(self, open_id: str) -> str:
        code = "code-" + secrets.token_urlsafe(8)
        self.codes[code] = open_id
        return code

    def expire_access_tokens(self) -> None:
        self.access_tokens = {k: (v[0], 0.0) for k, v in self.access_tokens.items()}

    def fail(self, path: str, *responses: Any) -> None:
        self.faults.setdefault(path, []).extend(responses)

    # --- обработка запросов --------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        fault = self.faults.get(path)
        if fault:
            item = fault.pop(0)
            if item == "network":
                raise httpx.ConnectError("emulated network failure", request=request)
            status, code = item
            return self._error(status, code)
        try:
            if path == "/v2/auth/authorize/":
                return self._authorize_page(request)
            if path == "/v2/auth/authorize/confirm":
                return self._authorize_confirm(request)
            if path == "/v2/oauth/token/":
                return self._token(request)
            if path == "/v2/oauth/revoke/":
                return httpx.Response(200, json={})
            if path == "/v2/post/publish/creator_info/query/":
                return self._creator_info(request)
            if path == "/v2/post/publish/video/init/":
                return self._init(request)
            if path == "/v2/post/publish/status/fetch/":
                return self._status(request)
            m = re.fullmatch(r"/upload/([^/]+)", path)
            if m and request.method == "PUT":
                return self._upload(request, m.group(1))
        except DropResponse:
            raise httpx.ReadError("emulated dropped response", request=request) from None
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    # --- OAuth ---------------------------------------------------------------------------

    def _authorize_page(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        rows = []
        for creator in self.creators.values():
            link = "/v2/auth/authorize/confirm?" + urlencode(
                {
                    "open_id": creator.open_id,
                    "redirect_uri": q.get("redirect_uri", ""),
                    "state": q.get("state", ""),
                    "scope": q.get("scope", ""),
                }
            )
            rows.append(
                f'<a class="acc" href="{html.escape(link)}"><b>{html.escape(creator.nickname)}</b>'
                f"<span>@{html.escape(creator.username)}</span></a>"
            )
        deny = (
            q.get("redirect_uri", "")
            + "?"
            + urlencode(
                {
                    "error": "access_denied",
                    "error_description": "Пользователь отменил вход",
                    "state": q.get("state", ""),
                }
            )
        )
        page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Эмулятор TikTok — вход</title>
<style>body{{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;justify-content:center;padding:40px 16px}}
main{{max-width:420px;width:100%}}.note{{background:#3a2a00;color:#ffd27a;padding:10px 12px;border-radius:8px;font-size:14px}}
.acc{{display:flex;justify-content:space-between;padding:14px;margin:8px 0;background:#222;border-radius:8px;color:#fff;text-decoration:none}}
.acc:hover{{background:#333}}.acc span{{color:#aaa}}a.deny{{color:#aaa;display:block;margin-top:16px}}</style></head>
<body><main><h1>Эмулятор TikTok</h1>
<p class="note">Это локальный эмулятор для разработки, а не настоящий TikTok.</p>
<p>Приложение запрашивает доступ: <code>{html.escape(q.get("scope", ""))}</code>. Выберите аккаунт:</p>
{"".join(rows)}<a class="deny" href="{html.escape(deny)}">Отказать</a></main></body></html>"""
        return httpx.Response(200, html=page)

    def _authorize_confirm(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        code = self.issue_code(q["open_id"])
        location = (
            q["redirect_uri"]
            + "?"
            + urlencode({"code": code, "scopes": q.get("scope", ""), "state": q.get("state", "")})
        )
        return httpx.Response(302, headers={"Location": location})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_key") != self.client_key or form.get("client_secret") != self.client_secret:
            return self._oauth_error("invalid_client", "Client key or secret is incorrect")
        grant = form.get("grant_type")
        if grant == "authorization_code":
            open_id = self.codes.pop(form.get("code", ""), None)
            if open_id is None:
                return self._oauth_error("invalid_grant", "Authorization code is expired")
        elif grant == "refresh_token":
            open_id = self.refresh_tokens.get(form.get("refresh_token", ""))
            if open_id is None:
                return self._oauth_error("invalid_grant", "Refresh token is invalid or expired")
        else:
            return self._oauth_error("invalid_request", "unsupported grant_type")
        access = "act." + secrets.token_urlsafe(16)
        refresh = "rft." + secrets.token_urlsafe(16)
        self.access_tokens[access] = (open_id, self.clock() + self.access_ttl_s)
        self.refresh_tokens[refresh] = open_id
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "expires_in": self.access_ttl_s,
                "open_id": open_id,
                "refresh_expires_in": 31536000,
                "refresh_token": refresh,
                "scope": "user.info.basic,video.publish",
                "token_type": "Bearer",
            },
        )

    # --- Content Posting API -------------------------------------------------------------

    def _auth(self, request: httpx.Request) -> FakeCreator | None:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        entry = self.access_tokens.get(token)
        if entry is None or entry[1] <= self.clock():
            return None
        return self.creators.get(entry[0])

    def _creator_info(self, request: httpx.Request) -> httpx.Response:
        creator = self._auth(request)
        if creator is None:
            return self._error(401, "access_token_invalid")
        options = ["SELF_ONLY"] if self.unaudited else creator.privacy_level_options
        return self._ok(
            {
                "creator_avatar_url": "",
                "creator_username": creator.username,
                "creator_nickname": creator.nickname,
                "privacy_level_options": options,
                "comment_disabled": creator.comment_disabled,
                "duet_disabled": creator.duet_disabled,
                "stitch_disabled": creator.stitch_disabled,
                "max_video_post_duration_sec": creator.max_video_post_duration_sec,
            }
        )

    def _init(self, request: httpx.Request) -> httpx.Response:
        creator = self._auth(request)
        if creator is None:
            return self._error(401, "access_token_invalid")
        body = httpx.Response(200, content=request.content).json()
        post_info = body.get("post_info", {})
        source = body.get("source_info", {})
        privacy = post_info.get("privacy_level")
        if self.unaudited and privacy != "SELF_ONLY":
            return self._error(403, "unaudited_client_can_only_post_to_private_accounts")
        if privacy not in creator.privacy_level_options:
            return self._error(400, "privacy_level_option_mismatch")
        if source.get("source") != "FILE_UPLOAD":
            return self._error(400, "invalid_param", "only FILE_UPLOAD is emulated")
        size, chunk, total = (
            source.get("video_size"),
            source.get("chunk_size"),
            source.get("total_chunk_count"),
        )
        if not all(isinstance(v, int) and v > 0 for v in (size, chunk, total)):
            return self._error(400, "invalid_param", "video_size/chunk_size/total_chunk_count")
        if size <= MAX_CHUNK:
            valid = total == 1 and chunk == size
        else:
            valid = MIN_CHUNK <= chunk <= MAX_CHUNK and total == size // chunk
        if not valid:
            return self._error(400, "invalid_param", "chunk rules violated")
        now = self.clock()
        if self.unaudited:
            active = {k for k, t in self.posting_creators.items() if now - t < 86400}
            if creator.open_id not in active and len(active) >= 5:
                return self._error(403, "reached_active_user_cap")
        self.posting_creators[creator.open_id] = now
        publish_id = f"v_pub_file~v2-1.{next(self._ids)}"
        self.publishes[publish_id] = FakePublish(
            publish_id=publish_id,
            open_id=creator.open_id,
            post_info=post_info,
            video_size=size,
            chunk_size=chunk,
            total_chunks=total,
            fail_reason=self.fail_reason_for_next_publish,
        )
        self.fail_reason_for_next_publish = None
        return self._ok({"publish_id": publish_id, "upload_url": f"{self.api_base}/upload/{publish_id}"})

    def _upload(self, request: httpx.Request, publish_id: str) -> httpx.Response:
        pub = self.publishes.get(publish_id)
        if pub is None:
            return httpx.Response(404, text="unknown upload")
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", request.headers.get("Content-Range", ""))
        if not m:
            return httpx.Response(400, text="bad Content-Range")
        start, end, total = map(int, m.groups())
        if total != pub.video_size or end >= total or len(request.content) != end - start + 1:
            return httpx.Response(400, text="range mismatch")
        index = start // pub.chunk_size if pub.total_chunks > 1 else 0
        index = min(index, pub.total_chunks - 1)
        item = None
        fault = self.upload_faults.get(index)
        if fault:
            item = fault.pop(0)
            if isinstance(item, int):
                return httpx.Response(item, text="emulated upload failure")
            if item == "drop_before":
                raise DropResponse
        pub.ranges.add((start, end))
        if pub.uploaded_bytes >= pub.video_size and pub.completed_at is None:
            pub.completed_at = self.clock()
            pub.post_id = 7_000_000_000_000_000_000 + next(self._ids)
        if item == "drop_after":
            raise DropResponse
        return httpx.Response(201 if pub.completed_at else 206)

    def _status(self, request: httpx.Request) -> httpx.Response:
        creator = self._auth(request)
        if creator is None:
            return self._error(401, "access_token_invalid")
        body = httpx.Response(200, content=request.content).json()
        pub = self.publishes.get(body.get("publish_id", ""))
        if pub is None or pub.open_id != creator.open_id:
            return self._error(400, "invalid_publish_id")
        if pub.completed_at is None:
            return self._ok({"status": "PROCESSING_UPLOAD", "uploaded_bytes": pub.uploaded_bytes})
        if self.clock() - pub.completed_at < self.processing_delay_s:
            return self._ok({"status": "PROCESSING_DOWNLOAD", "uploaded_bytes": pub.uploaded_bytes})
        if pub.fail_reason:
            return self._ok({"status": "FAILED", "fail_reason": pub.fail_reason})
        public = pub.post_info.get("privacy_level") == "PUBLIC_TO_EVERYONE"
        return self._ok(
            {
                "status": "PUBLISH_COMPLETE",
                "publicaly_available_post_id": [pub.post_id] if public else [],
                "uploaded_bytes": pub.uploaded_bytes,
            }
        )

    # --- ответы --------------------------------------------------------------------------

    @staticmethod
    def _log_id() -> str:
        return "emu" + secrets.token_hex(6)

    def _ok(self, data: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, json={"data": data, "error": {"code": "ok", "message": "", "log_id": self._log_id()}}
        )

    def _error(self, status: int, code: str, message: str = "") -> httpx.Response:
        return httpx.Response(
            status,
            json={"data": {}, "error": {"code": code, "message": message or code, "log_id": self._log_id()}},
        )

    def _oauth_error(self, code: str, description: str) -> httpx.Response:
        return httpx.Response(
            400, json={"error": code, "error_description": description, "log_id": self._log_id()}
        )
