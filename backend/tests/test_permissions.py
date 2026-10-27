"""Матрица прав (ТЗ раздел 9, «Права доступа»): каждый эндпоинт × каждая роль."""
import pytest

ROLES = ["owner", "manager", "technologist", "foreman", "worker", "viewer", "director"]
O, M, T, F, W, V, D = ROLES
MANAGERS, STAFF, READERS, ALL = {O, M}, {O, M, F}, {O, M, T, F, V, D}, set(ROLES)
CATALOG = {O, M, T}          # каталог изделий: создание и редактирование

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
    ("POST", "/workers", {"name": "W-perm"}, STAFF),
    ("POST", "/products", {"name": "Prod-perm"}, CATALOG),
    ("GET", "/routings", None, READERS),
    ("PATCH", "/routings/999999", {"name": "x"}, CATALOG),
    ("GET", "/order-lines/999999/operations", None, READERS),
    ("PUT", "/order-lines/999999/operations", {"assignments": []}, MANAGERS),
    ("GET", "/order-distribution", None, MANAGERS),
    ("GET", "/reports/order-status?order_line_id=999999", None, READERS),
    ("GET", "/reports/overview", None, READERS),
    ("POST", "/plan-day/items", {"plan_date": "2099-01-01", "worker_id": 1, "order_line_id": 1, "operation_id": 1, "qty": 1}, STAFF),
    ("PATCH", "/plan-day/items/999999", {"qty": 1}, STAFF),
    ("DELETE", "/plan-day/items/999999", None, STAFF),
    ("DELETE", "/plan-day/pins?worker_id=1&plan_date=2099-01-01", None, STAFF),
    ("POST", "/plan-day/pins", {"worker_id": 1, "plan_date": "2099-01-01"}, STAFF),
    ("GET", "/plan-grid/orders", None, STAFF),
    ("GET", "/plan-grid?order_line_id=999999", None, STAFF),
    ("POST", "/plan-grid/assign", {"order_line_id": 999999, "operation_id": 1, "plan_date": "2099-01-01", "worker_id": 1}, STAFF),
    ("DELETE", "/plan-grid/assign/999999", None, STAFF),
    ("POST", "/plan-day/workers", {"name": "Матрица-врем", "work_center_id": 1}, STAFF),
    ("POST", "/extra-tasks", {"plan_date": "2099-01-01", "worker_id": 1, "title": "x", "hours": 1}, STAFF),
    ("PATCH", "/extra-tasks/999999", {"title": "y"}, STAFF),
    ("DELETE", "/extra-tasks/999999", None, STAFF),
    ("PUT", "/extra-tasks/999999/fact", {"fact_hours": 1}, ALL - {"technologist", "viewer", "director"}),
    ("POST", "/order-lines", {"project_id": 999999, "product_id": 999999, "qty": 1}, MANAGERS),
    ("POST", "/holidays", {"holiday_date": "2026-12-31"}, MANAGERS),
    ("POST", "/absences", {"worker_id": 999999, "date_from": "2026-10-05", "date_to": "2026-10-05"}, STAFF),
    ("PUT", "/workers/999999/access", {"order_line_ids": []}, STAFF),
    ("POST", "/products/999999/routings", None, CATALOG),
    ("POST", "/routings/999999/operations", {"name": "x", "norm_per_day": 1}, CATALOG),
    ("PATCH", "/operations/999999", {"hold_qty": 1}, CATALOG),
    ("DELETE", "/operations/999999", None, CATALOG),
    ("PATCH", "/settings", {"max_workers_per_op": 4}, MANAGERS),
    ("GET", "/audit", None, MANAGERS),
    ("GET", "/telegram/status", None, STAFF),
    ("POST", "/telegram/codes", {"worker_id": 999999}, MANAGERS),
    ("DELETE", "/telegram/links/999999", None, STAFF),
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
    ("POST", "/users/import", {"rows": [{"email": "bad", "first_name": "А", "last_name": "Б", "role": "viewer"}], "dry_run": True}, {O}),
    ("POST", "/products/import", {"rows": [{"product": "Imp-perm", "operation": "Оп", "norm_per_day": "5"}], "dry_run": True}, CATALOG),
    ("PATCH", "/auth/profile", {}, ALL),
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
        covered.add((m, "/api/v1" + re.sub(r"\d+", "{}", p.split("?")[0])))
    crud = {"work-centers", "fact-reasons", "projects", "workers", "absences", "holidays", "products", "order-lines"}
    missing = []
    # маршруты берём из описания API: в новых версиях FastAPI подключённые роутеры лежат во вложенных обёртках,
    # и обход app.routes ничего бы не находил (проверка стала бы пустой)
    paths = app.openapi()["paths"]
    assert len(paths) > 50, "описание API пустое — проверка маршрутов не сработает"
    for path, ops in paths.items():
        if not path.startswith("/api/v1") or "openapi" in path:
            continue
        if path.startswith("/api/v1/tgapp/"):
            continue                                  # свой вход (подпись Telegram → токен сессии); закрытость проверяет test_tgapp
        for m in {k.upper() for k in ops} - {"HEAD", "OPTIONS"}:
            norm = re.sub(r"\{[^}]+\}", "{}", path)
            if (m, path) in open_routes:
                continue
            seg = path.split("/")[3] if len(path.split("/")) > 3 else ""
            if seg in crud:   # CRUD-ресурсы проверены целиком в test_isolation и матрицей на примерах
                continue
            if (m, norm) not in covered:
                missing.append((m, path))
    assert not missing, f"эндпоинты без проверки прав: {missing}"
