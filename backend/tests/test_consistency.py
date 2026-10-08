"""Согласованность данных: коммит до ответа, снимок при чтении, устойчивость расчёта к рассогласованным строкам."""
import socket, threading, time

import httpx
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from plan24 import db as dbm
from plan24.models import OrderLine, Product, Project, Routing, Tenant, PlannerSettings, User, Operation


@pytest.fixture()
def live(tmp_path, monkeypatch, request):
    """Настоящий HTTP-сервер: TestClient ждёт завершения всего ASGI-цикла и скрывает гонки после ответа."""
    import uvicorn
    monkeypatch.setenv("PLAN24_DATA", str(tmp_path)); monkeypatch.setenv("PLAN24_SECRET", "s" * 40)
    monkeypatch.setenv("PLAN24_ENV", "dev"); monkeypatch.setenv("PLAN24_MAIL_BACKEND", "memory")
    monkeypatch.setenv("PLAN24_TODAY", "2026-10-02")          # dev-переопределение «сегодня»
    from conftest import init_test_db
    init_test_db(tmp_path, request)
    from argon2 import PasswordHasher
    from plan24 import security
    monkeypatch.setattr(security, "_ph", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    from plan24.app import create_app
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1); break
        except Exception:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True; t.join(timeout=5)


def test_commit_happens_before_response_is_sent(live, monkeypatch):
    """Регрессия: если коммит выполняется после отправки ответа, клиент, мгновенно читающий записанное, его не видит.
    Коммит искусственно замедлен — при старом поведении чтение гарантированно промахивается."""
    c = httpx.Client(base_url=live, timeout=10)
    r = c.post("/api/v1/auth/register", json={"org_name": "Цех Гонка", "name": "Гонка Тест", "email": "r@r.ru",
                                              "password": "password-1", "accept_terms": True})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    real_commit = Session.commit

    def slow_commit(self):
        time.sleep(0.4)
        return real_commit(self)
    monkeypatch.setattr(Session, "commit", slow_commit)
    p = c.post("/api/v1/projects", json={"name": "Сразу виден"}, headers=h)
    assert p.status_code == 201
    with httpx.Client(base_url=live, timeout=10) as other:                 # другое соединение, сразу после ответа
        g = other.get(f"/api/v1/projects/{p.json()['id']}", headers=h)
    assert g.status_code == 200, "ответ отправлен раньше коммита — клиент читает несохранённые данные"


@pytest.mark.skipif(__import__("conftest").PG_MODE, reason="проверка снимка SQLite")
def test_sqlite_reader_sees_one_consistent_snapshot(tmp_path):
    dbm.init(f"sqlite:///{tmp_path / 'snap.sqlite3'}")
    s1, s2 = dbm._Session(), dbm._Session()      # именно обычные сессии: проверяется снимок чтения SQLite
    try:
        t = Tenant(name="T"); s2.add(t); s2.commit()
        assert s1.scalar(select(func.count()).select_from(Product)) == 0       # читатель начал транзакцию
        s2.add(Product(tenant_id=t.id, name="Новое")); s2.commit()             # писатель зафиксировал между чтениями
        assert s1.scalar(select(func.count()).select_from(Product)) == 0       # читатель всё ещё видит свой снимок
        s1.rollback()
        assert s1.scalar(select(func.count()).select_from(Product)) == 1
    finally:
        s1.close(); s2.close()


def test_planning_skips_rows_with_foreign_or_missing_references(env):
    """Расчёт не падает на позиции, ссылающейся на изделие другой организации (рассогласование/порча данных)."""
    from conftest import Org, TODAY
    from plan24 import planning
    a, b = Org(env, "a@a.ru", "Цех А"), Org(env, "b@b.ru", "Цех Б")
    a.seed("furniture"); b.seed("windows")
    s = dbm.admin_session()
    try:
        ta = s.get(User, a.data["user"]["id"]).tenant_id
        foreign = s.scalars(select(Product).where(Product.tenant_id != ta)).first()
        foreign_rt = s.scalars(select(Routing).where(Routing.product_id == foreign.id)).first()
        proj = s.scalars(select(Project).where(Project.tenant_id == ta)).first()
        s.add(OrderLine(tenant_id=ta, project_id=proj.id, product_id=foreign.id, routing_id=foreign_rt.id,
                        ext_id="BAD", qty=5, priority=1))
        s.commit()
        inp, ctx = planning.build_input(s, ta, TODAY)
        assert "BAD" not in ctx.order_by_key and len(inp.order_lines) == 8       # чужая ссылка отброшена
    finally:
        s.close()
    assert a.post("/plans/replan").status_code == 201


def test_concurrent_writers_and_readers_never_get_server_errors(live):
    """Параллельные входы, записи, факт и перепланирование не должны давать 500 («database is locked»)."""
    base = live
    c = httpx.Client(base_url=base, timeout=30)
    r = c.post("/api/v1/auth/register", json={"org_name": "Цех Нагрузка", "name": "Нагрузка Тест", "email": "load@x.ru",
                                              "password": "password-1", "accept_terms": True})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert c.post("/api/v1/demo/load?kind=furniture", headers=h).status_code == 201
    assert c.post("/api/v1/plans/replan", headers=h, json={}).status_code == 201
    day = c.get("/api/v1/reports/day", headers=h).json()
    w = day["workers"][0]; t = w["tasks"][0]
    bad, lock = [], threading.Lock()

    def worker(n):
        cl = httpx.Client(base_url=base, timeout=30)
        for i in range(8):
            calls = [
                ("POST", "/api/v1/projects", {"name": f"Нагр-{n}-{i}"}),
                ("GET", "/api/v1/projects", None),
                ("POST", "/api/v1/auth/login", {"email": "load@x.ru", "password": "password-1"}),
                ("POST", "/api/v1/facts", {"fact_date": day["date"], "order_line_id": t["order_line_id"],
                                           "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": n + i}),
                ("GET", "/api/v1/plans/active", None),
            ]
            if i % 4 == 0:
                calls.append(("POST", "/api/v1/plans/replan", {}))
            for m, p, b in calls:
                x = cl.request(m, p, headers=h, **({"json": b} if b is not None else {}))
                if x.status_code >= 500:
                    with lock:
                        bad.append((m, p, x.status_code, x.text[:120]))
    ths = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [th.start() for th in ths]; [th.join() for th in ths]
    assert not bad, bad[:3]
    # данные целы: все 48 проектов на месте, факт один (повторный ввод заменяет значение)
    names = [x["name"] for x in c.get("/api/v1/projects?limit=1000", headers=h).json()["items"] if x["name"].startswith("Нагр-")]
    assert len(names) == 48 and len(set(names)) == 48
    facts = c.get("/api/v1/facts", headers=h).json()["items"]
    assert len([f for f in facts if f["worker_id"] == w["worker_id"] and f["operation_id"] == t["operation_id"]
                and f["order_line_id"] == t["order_line_id"] and f["fact_date"] == day["date"]]) == 1
