"""Открытые страницы «Условия использования» и «Политика конфиденциальности».

Их адреса указываются в настройках приложения TikTok, поэтому они доступны без входа.
Владелец, email и страна берутся из .env (LEGAL_OWNER, LEGAL_EMAIL, LEGAL_COUNTRY).
"""

import html
import math
from pathlib import Path
from string import Template

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.config import get_settings

router = APIRouter(tags=["legal"], include_in_schema=False)

PAGES = Path(__file__).resolve().parent.parent / "legal"
# Дата текущей редакции документов: при изменении текста её нужно обновить.
EFFECTIVE = {"en": "31 October 2026", "ru": "31 октября 2026 г."}
TITLES = {
    "terms": {"en": "Terms of Service", "ru": "Условия использования"},
    "privacy": {"en": "Privacy Policy", "ru": "Политика конфиденциальности"},
}
SIGNIN = {
    True: {
        "en": "Only authorized team members can sign in, using two-factor authentication.",
        "ru": "Войти в Сервис могут только допущенные участники команды, с двухфакторным входом.",
    },
    False: {
        "en": "Only authorized team members can sign in.",
        "ru": "Войти в Сервис могут только допущенные участники команды.",
    },
}

LAYOUT = Template("""<!doctype html>
<html lang="$lang">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title — $app</title>
<link rel="icon" href="/icons/favicon-32.png">
<style>
  :root { color-scheme: light dark; --bg: #f6f7f9; --surface: #fff; --text: #171a1f; --muted: #667085;
    --border: #e3e6eb; --accent: #4f46e5; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #111317; --surface: #181b20; --text: #e8eaed; --muted: #9aa3b2; --border: #2a2f37;
      --accent: #8b85ff; }
  }
  body { margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header, main, footer { max-width: 760px; margin: 0 auto; padding: 0 20px; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 20px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 650; color: var(--text);
    text-decoration: none; }
  .brand img { width: 32px; height: 32px; border-radius: 8px; }
  main { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    margin-top: 16px; padding: 8px 28px 24px; }
  h1 { font-size: 28px; line-height: 1.25; margin: 20px 0 4px; }
  h2 { font-size: 19px; margin: 28px 0 8px; }
  h3 { font-size: 16px; margin: 18px 0 6px; }
  .meta { color: var(--muted); margin-top: 0; }
  a { color: var(--accent); }
  ul { padding-left: 22px; }
  li { margin: 4px 0; }
  footer { color: var(--muted); font-size: 14px; padding-top: 16px; padding-bottom: 32px;
    display: flex; gap: 16px; flex-wrap: wrap; }
  @media (max-width: 600px) {
    main { padding: 4px 16px 16px; border-radius: 0; border-left: 0; border-right: 0; }
    header, footer { padding-left: 16px; padding-right: 16px; } h1 { font-size: 24px; } }
</style>
</head>
<body>
<header>
  <a class="brand" href="/"><img src="/icons/icon-192.png" alt="">$app</a>
  <a href="$switch_href" lang="$switch_lang">$switch_label</a>
</header>
<main>
$body
</main>
<footer>
  <a href="$terms_href">$terms_title</a>
  <a href="$privacy_href">$privacy_title</a>
  <a href="mailto:$email">$email</a>
</footer>
</body>
</html>
""")


def _href(page: str, lang: str) -> str:
    return f"/{page}" if lang == "en" else f"/{page}?lang={lang}"


def _render(page: str, lang: str):
    settings = get_settings()
    if not (settings.legal_owner and settings.legal_email and settings.legal_country):
        return PlainTextResponse(
            "Страница не настроена: укажите LEGAL_OWNER, LEGAL_EMAIL и LEGAL_COUNTRY в .env",
            status_code=404,
        )
    lang = lang if lang in ("en", "ru") else "en"
    other = "ru" if lang == "en" else "en"
    values = {
        "app": settings.legal_app_name,
        "owner": settings.legal_owner,
        "email": settings.legal_email,
        "country": settings.legal_country,
        "country_ru": settings.legal_country_ru or settings.legal_country,
    }
    # Всё, что приходит из настроек, экранируется: это текст, а не разметка.
    safe = {k: html.escape(v) for k, v in values.items()}
    backup_days = math.ceil(settings.backup_keep * settings.backup_interval_hours / 24)
    body = Template((PAGES / f"{page}.{lang}.html").read_text(encoding="utf-8")).substitute(
        **safe,
        date=EFFECTIVE["en"],
        date_ru=EFFECTIVE["ru"],
        backup_days=backup_days,
        signin=SIGNIN[settings.require_2fa][lang],
    )
    return HTMLResponse(
        LAYOUT.substitute(
            lang=lang,
            title=TITLES[page][lang],
            app=safe["app"],
            email=safe["email"],
            body=body,
            switch_href=_href(page, other),
            switch_lang=other,
            switch_label="Русский" if other == "ru" else "English",
            terms_href=_href("terms", lang),
            terms_title=TITLES["terms"][lang],
            privacy_href=_href("privacy", lang),
            privacy_title=TITLES["privacy"][lang],
        )
    )


@router.get("/terms")
def terms(lang: str = "en"):
    return _render("terms", lang)


@router.get("/privacy")
def privacy(lang: str = "en"):
    return _render("privacy", lang)
