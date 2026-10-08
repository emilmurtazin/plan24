"""Матрица прав (ТЗ раздел 9, «Права доступа»): каждый эндпоинт × каждая роль."""
import pytest

ROLES = ["owner", "manager", "foreman", "worker", "viewer"]
O, M, F, W, V = ROLES
MANAGERS, STAFF, READERS, ALL = {O, M}, {O, M, F}, {O, M, F, V}, set(ROLES)

# (метод, путь, тело, кому разрешено). Для разрешённых допустим любой статус, кроме 401/403
# (например 404 на несуществующий id или 409/422 по бизнес-правилам) — проверяется именно граница прав.
CASES = [
    ("GET", "/auth/me", None, ALL),
    ("POST", "/auth/resend-verification", None, ALL),
    ("POST", "/users/999999/invite", None, {O}),
    ("GET", "/plans/active", None, ALL),
    ("GET", "/reports/day", None, ALL),
    ("GET", "/reports/status", None, READERS),
    ("GET", "/reports/load", None, READERS),
    ("GET", "/reports/dashboard", None, READERS),
    ("GET", "/workers", None, READERS),
    ("GET", "/products", None, READERS),
    ("GET", "/order-lines", None, READERS),
    ("GET", "/projects", None, READERS),
    ("GET", "/work-centers", None, READERS),
    ("GET", "/settings", None, READERS),
    ("GET", "/fact-reasons", None, ALL),
    ("GET", "/workers/999999/access", None, READERS),
    ("POST", "/projects", {"name": "P-perm"}, MANAGERS),
    ("PATCH", "/projects/999999", {"name": "x"}, MANAGERS),
    ("DELETE", "/projects/999999", None, MANAGERS),
    ("POST", "/work-centers", {"name": "WC-perm"}, MANAGERS),
    ("POST", "/workers", {"name": "W-perm"}, MANAGERS),
    ("POST", "/products", {"name": "Prod-perm"}, MANAGERS),
    ("POST", "/order-lines", {"project_id": 999999, "product_id": 999999, "qty": 1}, MANAGERS),
    ("POST", "/holidays", {"holiday_date": "2026-12-31"}, MANAGERS),
    ("POST", "/absences", {"worker_id": 999999, "date_from": "2026-10-05", "date_to": "2026-10-05"}, MANAGERS),
    ("PUT", "/workers/999999/access", {"order_line_ids": []}, MANAGERS),
    ("POST", "/products/999999/routings", None, MANAGERS),
    ("POST", "/routings/999999/operations", {"name": "x", "norm_per_day": 1}, MANAGERS),
    ("PATCH", "/operations/999999", {"hold_qty": 1}, MANAGERS),
    ("DELETE", "/operations/999999", None, MANAGERS),
    ("PATCH", "/settings", {"max_workers_per_op": 4}, MANAGERS),
    ("GET", "/audit", None, MANAGERS),
    ("GET", "/telegram/status", None, MANAGERS),
    ("POST", "/telegram/codes", {"worker_id": 999999}, MANAGERS),
    ("DELETE", "/telegram/links/999999", None, MANAGERS),
    ("POST", "/plans/999999/activate", None, MANAGERS),
    ("POST", "/demo/load", None, MANAGERS),
    ("POST", "/plans/replan", {}, STAFF),
    ("POST", "/plans/compare", {}, STAFF),
    ("GET", "/plans", None, STAFF),
    ("POST", "/facts/999999/confirm", None, STAFF),
    ("DELETE", "/facts/999999", None, STAFF),
    ("POST", "/facts", {"fact_date": "2026-10-02", "order_line_id": 999999, "operation_id": 1, "worker_id": 1, "qty": 1},
     {O, M, F, W}),
    ("GET", "/facts", None, {O, M, F, W}),
    ("GET", "/users", None, {O}),
    ("POST", "/users", {"email": "z@a.ru", "name": "Тест Тестов", "role": "viewer", "password": "password-1"}, {O}),
    ("PATCH", "/users/999999", {"name": "Тест Тестов"}, {O}),
]


@pytest.mark.parametrize("method,path,body,allowed", CASES, ids=[f"{m} {p}" for m, p, _, _ in CASES])
def test_matrix(full_org, method, path, body, allowed):
    for role in ROLES:
        kw = {"json": body} if body is not None else {}
        r = full_org.call(method, path, role, **kw)
        if role in allowed:
            assert r.status_code not in (401, 403), f"{role} должен иметь доступ к {method} {path}: {r.status_code} {r.text}"
        else:
            assert r.status_code == 403, f"{role} не должен иметь доступ к {method} {path}: {r.status_code}"


def test_every_route_is_covered_by_matrix(full_org):
    """Новый эндпоинт без записи в матрице прав — провал: права нельзя забыть."""
    app = full_org.c.app
    open_routes = {("POST", "/api/v1/auth/register"), ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/refresh"),
                   ("POST", "/api/v1/auth/verify"), ("GET", "/api/v1/health"), ("GET", "/api/v1/health/ready"), ("POST", "/api/v1/auth/forgot-password"),
                   ("POST", "/api/v1/auth/reset-password"), ("GET", "/api/v1/dev/outbox"),
                   ("POST", "/api/v1/integrations/telegram/webhook/{secret}"), ("POST", "/api/v1/auth/logout"),
                   ("POST", "/api/v1/auth/change-password"), ("GET", "/api/v1/demo/kinds")}
    covered = set()
    import re
    for m, p, _, _ in CASES:
        covered.add((m, "/api/v1" + re.sub(r"\d+", "{}", p)))
    crud = {"work-centers", "fact-reasons", "projects", "workers", "absences", "holidays", "products", "order-lines"}
    missing = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/v1") or "openapi" in path:
            continue
        for m in getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}:
            norm = re.sub(r"\{[^}]+\}", "{}", path)
            if (m, path) in open_routes:
                continue
            seg = path.split("/")[3] if len(path.split("/")) > 3 else ""
            if seg in crud:   # CRUD-ресурсы проверены целиком в test_isolation и матрицей на примерах
                continue
            if (m, norm) not in covered:
                missing.append((m, path))
    assert not missing, f"эндпоинты без проверки прав: {missing}"
