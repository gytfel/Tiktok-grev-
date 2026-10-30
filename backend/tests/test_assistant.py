import json

import httpx
import pytest

from app import ai
from app.deps import get_ai
from app.models import Role, User
from app.security import hash_password


class FakeLLM:
    """OpenAI-совместимый /chat/completions: так отвечают Ollama, OpenRouter, Gemini и другие."""

    def __init__(self, content="", status=200):
        self.content = content
        self.status = status
        self.requests = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)}
        )
        if self.status != 200:
            return httpx.Response(self.status, text="error")
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": self.content}}]}
        )


def client_for(fake: FakeLLM, api_key="secret-key") -> ai.AIClient:
    return ai.AIClient(
        "http://llm.test/v1",
        "test-model",
        api_key,
        http=httpx.Client(transport=httpx.MockTransport(fake.handle)),
    )


@pytest.mark.parametrize(
    "answer, expected",
    [
        ('["Первый", "Второй", "Третий"]', ["Первый", "Второй", "Третий"]),
        ('Вот варианты:\n```json\n["А", "Б"]\n```\nУдачи!', ["А", "Б"]),
        (
            "1. Первый вариант\n2) Второй вариант\n- Третий\n\n• Четвёртый",
            ["Первый вариант", "Второй вариант", "Третий", "Четвёртый"],
        ),
        ('[{"title": "Идея", "description": "суть"}]', ["Идея — суть"]),
        ('["Повтор", "повтор", "«Кавычки»"]', ["Повтор", "Кавычки"]),
    ],
)
def test_parse_variants(answer, expected):
    assert ai.parse_variants(answer, 10) == expected


def test_parse_variants_respects_count():
    assert ai.parse_variants('["1", "2", "3", "4"]', 2) == ["1", "2"]


@pytest.mark.parametrize("preamble", ["Вот хештеги для видео:", "Хештеги:", "Конечно! Держите подборку"])
def test_hashtags_ignore_chatty_preamble(preamble):
    fake = FakeLLM(f"{preamble}\n1. #осень\n2. #скидки\n3. #шопинг")
    result = ai.generate(client_for(fake), ai.GenerateRequest(task="hashtags", topic="x", count=5))
    assert result == ["#осень", "#скидки", "#шопинг"]


def test_normalize_hashtags():
    assert ai.normalize_hashtags(["#Скидки", "sale #autumn", "#sale!", "", "#", "весна"], 10) == [
        "#Скидки",
        "#autumn",
        "#sale",
        "#весна",
    ]


def test_generate_sends_openai_compatible_request():
    fake = FakeLLM('["Подпись 1", "Подпись 2"]')
    result = ai.generate(
        client_for(fake), ai.GenerateRequest(task="caption", topic="осенняя распродажа", count=2)
    )
    assert result == ["Подпись 1", "Подпись 2"]
    req = fake.requests[0]
    assert req["url"] == "http://llm.test/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer secret-key"
    assert req["body"]["model"] == "test-model"
    assert "осенняя распродажа" in req["body"]["messages"][1]["content"]


def test_local_model_without_key_sends_no_auth_header():
    fake = FakeLLM('["ok"]')
    ai.generate(client_for(fake, api_key=""), ai.GenerateRequest(task="ideas", topic="кофе"))
    assert "authorization" not in fake.requests[0]["headers"]


def test_hashtags_are_normalized():
    fake = FakeLLM('["#осень", "скидки", "#осень", "#sale #fashion"]')
    assert ai.generate(client_for(fake), ai.GenerateRequest(task="hashtags", topic="x", count=3)) == [
        "#осень",
        "#скидки",
        "#sale",
    ]


@pytest.mark.parametrize(
    "status, message", [(401, "ключ"), (429, "лимит"), (404, "модель"), (500, "ошибку 500")]
)
def test_service_errors_are_explained(status, message):
    with pytest.raises(ai.AIError, match=message):
        ai.generate(client_for(FakeLLM(status=status)), ai.GenerateRequest(task="caption", topic="x"))


def test_empty_answer_is_an_error():
    with pytest.raises(ai.AIError):
        ai.generate(client_for(FakeLLM("   ")), ai.GenerateRequest(task="caption", topic="x"))


# --- API ---------------------------------------------------------------------------------


def test_ai_disabled_by_default(admin_client):
    assert admin_client.get("/api/ai/status").json() == {"enabled": False, "model": None}
    r = admin_client.post("/api/ai/generate", json={"task": "caption", "topic": "x"})
    assert r.status_code == 400 and "AI_BASE_URL" in r.json()["detail"]


def test_ai_generate_endpoint(admin_client):
    from app.main import app

    fake = FakeLLM('["Ответ 1", "Ответ 2"]')
    app.dependency_overrides[get_ai] = lambda: client_for(fake)
    r = admin_client.post("/api/ai/generate", json={"task": "reply", "comment": "Где купить?", "count": 2})
    assert r.status_code == 200 and r.json() == {"variants": ["Ответ 1", "Ответ 2"]}
    assert "Где купить?" in fake.requests[0]["body"]["messages"][1]["content"]

    assert admin_client.post("/api/ai/generate", json={"task": "reply", "comment": " "}).status_code == 422
    fake.status = 429
    r = admin_client.post("/api/ai/generate", json={"task": "caption", "topic": "x"})
    assert r.status_code == 502 and "лимит" in r.json()["detail"]
    actions = [e["action"] for e in admin_client.get("/api/audit").json()]
    assert "ai.generated" in actions


def test_templates_crud(admin_client):
    body = {"kind": "hashtags", "category": "Осень", "title": "Распродажа", "body": "#sale #autumn"}
    created = admin_client.post("/api/templates", json=body).json()
    admin_client.post("/api/templates", json={"kind": "caption", "title": "Приветствие", "body": "Привет!"})
    assert [t["title"] for t in admin_client.get("/api/templates?kind=hashtags").json()] == ["Распродажа"]
    assert len(admin_client.get("/api/templates").json()) == 2

    updated = admin_client.patch(f"/api/templates/{created['id']}", json={"body": "#sale"}).json()
    assert updated["body"] == "#sale" and updated["category"] == "Осень"
    assert admin_client.post("/api/templates", json={**body, "kind": "unknown"}).status_code == 422
    assert admin_client.delete(f"/api/templates/{created['id']}").status_code == 200
    assert len(admin_client.get("/api/templates").json()) == 1


def test_viewer_cannot_change_templates_or_generate(client, db):
    db.add(User(username="viewer", password_hash=hash_password("viewer-password"), role=Role.VIEWER))
    db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert client.get("/api/templates").status_code == 200
    assert (
        client.post("/api/templates", json={"kind": "caption", "title": "x", "body": "y"}).status_code == 403
    )
    assert client.post("/api/ai/generate", json={"task": "caption"}).status_code == 403
