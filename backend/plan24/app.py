"""Plan 24 — сервис: организации, роли, данные в системе, планирование. Префикс /api/v1 (ТЗ раздел 9)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import auth, demo, dev, facts, integrations, plans, refs, reports, users

STATIC = Path(__file__).resolve().parent.parent.parent / "frontend"


def problem(status: int, title: str, detail=None):
    """Ошибки в формате RFC 7807."""
    body = {"type": "about:blank", "title": title, "status": status}
    if detail is not None:
        body["errors"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def create_app() -> FastAPI:
    import os
    from . import config
    docs = config.env() == "dev" or os.environ.get("PLAN24_DOCS") == "1"      # в prod описание API не публикуется
    app = FastAPI(title="Plan 24", version="0.2.0", docs_url="/api/docs" if docs else None,
                  openapi_url="/api/openapi.json" if docs else None, redoc_url=None)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, e: HTTPException):
        return problem(e.status_code, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        errs = [{"field": ".".join(str(x) for x in err["loc"] if x not in ("body", "query")), "message": err["msg"]}
                for err in e.errors()]
        return problem(422, "Проверьте введённые данные", errs)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "same-origin"
        h["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        if config.env() == "prod":
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"      # по HTTP браузеры его игнорируют
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"          # ответы API содержат данные организации
        return resp

    @app.get("/api/v1/health")
    def health():
        """Процесс жив (для проверки «живости» оркестратора)."""
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    def ready():
        """Готов принимать запросы: БД отвечает, схема на последней ревизии миграций."""
        try:
            from sqlalchemy import text
            eng = db.get_engine()
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            if db.is_pg():
                cur, head = db.schema_revision()
                if cur != head:
                    return problem(503, "Схема БД не на последней ревизии", {"current": cur, "head": head})
            return {"status": "ready", "database": eng.dialect.name}
        except Exception as e:                                       # noqa: BLE001
            return problem(503, "База данных недоступна", type(e).__name__)

    for r in (auth.router, users.router, refs.router, plans.router, facts.router, reports.router, demo.router, dev.router, integrations.router):
        app.include_router(r, prefix="/api/v1")

    if STATIC.exists():
        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(STATIC / "index.html")
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
