"""Лендинг /welcome: открывается без входа, не тянет внешние ресурсы без настройки, счётчик подключается только по PLAN24_METRIKA_ID."""


def test_landing_is_public_and_links_to_registration(env):
    r = env.get("/welcome")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'href="/#register"' in r.text and "14 дней" not in r.text
    assert "https://mc.yandex.ru" not in r.headers["content-security-policy"]           # без настройки — никаких внешних адресов
    assert env.get("/static/landing/landing.css").status_code == 200 and env.get("/static/landing/landing.js").status_code == 200
    assert env.get("/welcome/config.js").text == 'window.P24_LANDING = {"metrika": ""};'


def test_metrika_only_when_configured(env, monkeypatch):
    monkeypatch.setenv("PLAN24_METRIKA_ID", "12345678")
    assert '"metrika": "12345678"' in env.get("/welcome/config.js").text
    assert "https://mc.yandex.ru" in env.get("/welcome").headers["content-security-policy"]
    assert "mc.yandex.ru" not in env.get("/").headers["content-security-policy"]         # само приложение счётчик не получает
    monkeypatch.setenv("PLAN24_METRIKA_ID", "abc\"<script>")
    assert env.get("/welcome/config.js").text == 'window.P24_LANDING = {"metrika": ""};'  # не цифры — игнорируется
