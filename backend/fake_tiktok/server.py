"""HTTP-сервер эмулятора: uvicorn fake_tiktok.server:app --port 9000

Переменные окружения:
  EMULATOR_API_BASE        адрес, по которому воркер обращается к эмулятору (для upload_url)
  EMULATOR_CLIENT_KEY      ожидаемый client_key (по умолчанию test-client-key)
  EMULATOR_CLIENT_SECRET   ожидаемый client_secret (по умолчанию test-client-secret)
  EMULATOR_UNAUDITED       1 — вести себя как приложение без аудита (только SELF_ONLY, 5 авторов в сутки)
  EMULATOR_PROCESSING_S    сколько секунд «обрабатывать» видео после загрузки
"""

import os

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from fake_tiktok.core import FakeTikTok

fake = FakeTikTok(
    os.environ.get("EMULATOR_API_BASE", "http://localhost:9000"),
    client_key=os.environ.get("EMULATOR_CLIENT_KEY", "test-client-key"),
    client_secret=os.environ.get("EMULATOR_CLIENT_SECRET", "test-client-secret"),
    unaudited=os.environ.get("EMULATOR_UNAUDITED") == "1",
    processing_delay_s=float(os.environ.get("EMULATOR_PROCESSING_S", "4")),
)
for username, nickname in (
    ("studio_main", "Studio Main"),
    ("studio_backup", "Studio Backup"),
    ("brand_ru", "Brand RU"),
    ("brand_kz", "Brand KZ"),
    ("news_daily", "News Daily"),
    ("shop_promo", "Shop Promo"),
    ("friends_only", "Friends Only"),
    ("short_clips", "Short Clips"),
):
    kwargs = {}
    if username == "friends_only":
        kwargs = {"privacy_level_options": ["MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"], "comment_disabled": True}
    if username == "short_clips":
        kwargs = {"max_video_post_duration_sec": 15}
    fake.add_creator(username, nickname, **kwargs)


async def dispatch(request: Request) -> Response:
    body = await request.body()
    req = httpx.Request(request.method, str(request.url), headers=request.headers.raw, content=body)
    try:
        res = fake.handle(req)
    except httpx.TransportError:
        return Response(status_code=499)
    headers = {
        k: v for k, v in res.headers.items() if k.lower() not in ("content-length", "content-encoding")
    }
    return Response(content=res.content, status_code=res.status_code, headers=headers)


app = Starlette(
    routes=[Route("/{path:path}", dispatch, methods=["GET", "POST", "PUT"])],
)
