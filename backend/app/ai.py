"""AI-помощник для текстов: подписи, хештеги, идеи роликов, варианты ответов на комментарии.

Работает с любым сервисом, у которого OpenAI-совместимый API (POST {base}/chat/completions):
локальная Ollama, OpenRouter, Google Gemini, Groq, OpenAI и другие. Сервис и модель задаются
в .env (AI_BASE_URL, AI_MODEL, AI_API_KEY). Тексты только предлагаются: что использовать,
решает человек.
"""

import json
import re
from dataclasses import dataclass

import httpx

from app.config import Settings

TASKS = ("caption", "hashtags", "ideas", "reply")
MAX_COUNT = 10

SYSTEM_PROMPT = (
    "Ты помогаешь автору вести TikTok-аккаунт: пишешь короткие живые тексты. "
    "Отвечай только JSON-массивом строк, без пояснений и без нумерации."
)


class AIError(Exception):
    pass


@dataclass
class GenerateRequest:
    task: str
    topic: str = ""
    text: str = ""
    comment: str = ""
    tone: str = ""
    language: str = "русский"
    count: int = 5


def build_prompt(req: GenerateRequest) -> str:
    count = max(1, min(req.count, MAX_COUNT))
    tone = f" Тон: {req.tone}." if req.tone else ""
    lang = f" Язык: {req.language}." if req.language else ""
    if req.task == "caption":
        draft = f" Черновик автора: «{req.text}»." if req.text else ""
        return (
            f"Придумай {count} вариантов подписи к TikTok-видео. Тема: {req.topic or 'не указана'}."
            f"{draft}{tone}{lang} Каждая подпись до 300 символов, можно с эмодзи, без хештегов."
        )
    if req.task == "hashtags":
        about = req.topic or req.text or "не указана"
        return (
            f"Подбери {count} хештегов для TikTok-видео. Тема: {about}.{lang} "
            "Каждый элемент массива — один хештег, начинается с #, без пробелов."
        )
    if req.task == "ideas":
        return (
            f"Предложи {count} идей коротких видео для TikTok. Ниша или тема: {req.topic or 'не указана'}."
            f"{tone}{lang} Каждая идея — одна строка: название и в чём суть ролика."
        )
    if req.task == "reply":
        about = f" О чём видео: {req.topic}." if req.topic else ""
        return (
            f"Предложи {count} вариантов ответа автора видео на комментарий зрителя: «{req.comment}».{about}"
            f"{tone}{lang} Ответы вежливые, короткие (до 150 символов), без хештегов и без рекламы."
        )
    raise AIError(f"Неизвестная задача: {req.task}")


_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+\s*[.)])\s*")


def _flatten(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return " — ".join(str(v) for v in item.values() if v)
    return str(item)


def parse_variants(text: str, count: int) -> list[str]:
    """Достаёт варианты из ответа модели: JSON-массив или, если модель его не соблюла, список строк."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    items: list[str] = []
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            items = [_flatten(x) for x in parsed]
    if not items:
        items = [_BULLET.sub("", line) for line in cleaned.splitlines()]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip().strip('"«»').strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result[:count]


def normalize_hashtags(items: list[str], count: int) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in items:
        tokens = [t for t in re.split(r"[\s,;]+", item) if t]
        explicit = [t for t in tokens if t.startswith("#")]
        if explicit:
            tokens = explicit
        elif len(tokens) > 2 or item.rstrip().endswith(":"):
            # Фраза без # — пояснение модели вроде «Вот хештеги:», а не хештег.
            continue
        for token in tokens:
            word = re.sub(r"[^\w]", "", token.lstrip("#"))
            if word and word.lower() not in seen:
                seen.add(word.lower())
                tags.append(f"#{word}")
    return tags[:count]


class AIClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120,
        http: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=10))

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.Client | None = None) -> "AIClient":
        return cls(settings.ai_base_url, settings.ai_model, settings.ai_api_key, settings.ai_timeout_s, http)

    def chat(self, system: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.9,
        }
        try:
            r = self.http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIError(
                "AI-сервис не ответил вовремя. Попробуйте ещё раз или выберите модель меньше."
            ) from exc
        except httpx.TransportError as exc:
            raise AIError(f"AI-сервис недоступен: {exc}") from exc
        # Gemini отвечает на неверный ключ кодом 400, а не 401.
        if r.status_code in (401, 403) or (r.status_code == 400 and "API key" in r.text):
            raise AIError("AI-сервис отклонил ключ доступа (AI_API_KEY)")
        if r.status_code == 429:
            raise AIError("Исчерпан лимит AI-сервиса. Попробуйте позже.")
        if r.status_code == 404:
            raise AIError("AI-сервис не нашёл модель или адрес. Проверьте AI_BASE_URL и AI_MODEL.")
        if r.status_code >= 400:
            raise AIError(f"AI-сервис вернул ошибку {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError("Неожиданный ответ AI-сервиса") from exc


def generate(client: AIClient, req: GenerateRequest) -> list[str]:
    count = max(1, min(req.count, MAX_COUNT))
    answer = client.chat(SYSTEM_PROMPT, build_prompt(req))
    variants = parse_variants(answer, count * 3 if req.task == "hashtags" else count)
    if req.task == "hashtags":
        variants = normalize_hashtags(variants, count)
    if not variants:
        raise AIError("Модель не вернула ни одного варианта. Попробуйте ещё раз.")
    return variants
