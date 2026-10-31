import pytest

from app.config import get_settings


@pytest.fixture
def legal(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "legal_owner", "Semen <Rondarev>")
    monkeypatch.setattr(settings, "legal_email", "owner@example.com")
    monkeypatch.setattr(settings, "legal_country", "Ukraine")
    monkeypatch.setattr(settings, "legal_country_ru", "Украина")


def test_pages_need_owner_details(client):
    r = client.get("/terms")
    assert r.status_code == 404 and "LEGAL_OWNER" in r.text


def test_public_pages_without_login(client, legal, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_2fa", True)
    terms = client.get("/terms")
    assert terms.status_code == 200 and terms.headers["content-type"].startswith("text/html")
    assert "<h1>Terms of Service</h1>" in terms.text
    assert "governed by the laws of Ukraine" in terms.text
    # Значения из настроек — текст, а не разметка.
    assert "Semen &lt;Rondarev&gt;" in terms.text and "<Rondarev>" not in terms.text

    privacy = client.get("/privacy")
    assert "mailto:owner@example.com" in privacy.text
    assert "using two-factor authentication" in privacy.text
    assert "up to 14 days" in privacy.text
    assert '<a href="/privacy?lang=ru" lang="ru">Русский</a>' in privacy.text

    ru = client.get("/privacy?lang=ru")
    assert '<html lang="ru">' in ru.text and "Политика конфиденциальности" in ru.text
    assert "Semen &lt;Rondarev&gt;, Украина" in ru.text
    assert "$" not in ru.text  # все подстановки заполнены
    assert client.get("/terms?lang=xx").text.count("Terms of Service") >= 1
