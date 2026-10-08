"""Заголовки безопасности и общие свойства API."""
from conftest import Org


def test_security_headers_on_page_and_api(env):
    for path in ("/", "/api/v1/health"):
        h = env.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert env.get("/api/v1/health").headers["cache-control"] == "no-store"


def test_frontend_has_no_inline_scripts_or_external_resources(env):
    """CSP запрещает inline-скрипты и чужие хосты — интерфейс не должен от них зависеть."""
    import re
    html = env.get("/").text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
    assert "http://" not in html and "https://" not in html
    for f in ("app.js", "data.js"):
        js = env.get("/static/" + f).text
        assert "eval(" not in js and "document.write" not in js


def test_mass_assignment_tenant_id_is_ignored(env):
    a, b = Org(env, "a@a.ru", "Цех А"), Org(env, "b@b.ru", "Цех Б")
    tb = b.data["user"]["tenant"]["id"]
    r = a.post("/projects", json={"name": "Проект", "tenant_id": tb, "id": 9999})
    assert r.status_code == 201
    assert r.json()["id"] != 9999
    assert [p["name"] for p in a.get("/projects").json()["items"]] == ["Проект"]
    assert b.get("/projects").json()["items"] == []


def test_html_in_names_is_stored_verbatim_and_escaped_by_ui_contract(org):
    """Сервер хранит текст как есть; экранирование — обязанность интерфейса (esc() во всех шаблонах)."""
    r = org.post("/projects", json={"name": "<img src=x onerror=alert(1)>"})
    assert r.status_code == 201 and r.json()["name"] == "<img src=x onerror=alert(1)>"


def test_openapi_lists_only_versioned_api(env):
    paths = env.get("/api/openapi.json").json()["paths"]
    assert paths and all(p.startswith("/api/v1/") for p in paths)


def test_api_docs_are_not_published_in_prod(env, monkeypatch):
    from plan24.app import create_app
    from fastapi.testclient import TestClient
    assert env.get("/api/openapi.json").status_code == 200                   # dev
    monkeypatch.setenv("PLAN24_ENV", "prod")
    prod = TestClient(create_app())
    assert prod.get("/api/openapi.json").status_code == 404 and prod.get("/api/docs").status_code == 404
    monkeypatch.setenv("PLAN24_DOCS", "1")
    assert TestClient(create_app()).get("/api/openapi.json").status_code == 200   # включается явно


def test_hsts_only_in_prod(env, monkeypatch):
    """TLS завершается на стороне платформы/прокси, поэтому HSTS выдаёт приложение (только в prod)."""
    from plan24.app import create_app
    from fastapi.testclient import TestClient
    assert "strict-transport-security" not in env.get("/api/v1/health").headers                 # dev
    monkeypatch.setenv("PLAN24_ENV", "prod")
    h = TestClient(create_app()).get("/api/v1/health").headers
    assert h["strict-transport-security"].startswith("max-age=31536000")
