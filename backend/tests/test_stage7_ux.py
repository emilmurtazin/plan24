"""Доработки UX (этап 7): имя и фамилия отдельно, профиль, роли «Директор»/«Администратор», загрузка пользователей и
каталога из файла, ручное закрепление дня, показатели дашборда, человеко-смены."""
from datetime import timedelta

import pytest

from conftest import TODAY


# ------------------------------------------------------------------ имя и фамилия
def test_register_with_separate_names(org):
    r = org.c.post("/api/v1/auth/register", json={"org_name": "Новая", "first_name": "Иван", "last_name": "Петров",
                                                  "email": "ivan@a.ru", "password": "password-1", "accept_terms": True})
    assert r.status_code == 201, r.text
    u = r.json()["user"]
    assert (u["first_name"], u["last_name"], u["name"]) == ("Иван", "Петров", "Иван Петров")
    bad = org.c.post("/api/v1/auth/register", json={"org_name": "Ещё", "first_name": "Иван", "last_name": " ",
                                                    "email": "i2@a.ru", "password": "password-1", "accept_terms": True})
    assert bad.status_code == 422


def test_legacy_single_name_still_accepted_and_split(org):
    me = org.get("/auth/me").json()
    assert me["name"] == "Владелец" and me["first_name"] == "Владелец" and me["last_name"] == ""
    u = org.post("/users", json={"email": "x@a.ru", "name": "Анна Сидорова", "role": "viewer", "password": "password-1"}).json()
    assert (u["first_name"], u["last_name"]) == ("Анна", "Сидорова")


def test_user_create_and_patch_names(org):
    u = org.post("/users", json={"email": "k@a.ru", "first_name": "Кирилл", "last_name": "Орлов", "role": "director",
                                 "password": "password-1"})
    assert u.status_code == 201, u.text
    uid = u.json()["id"]
    assert u.json()["name"] == "Кирилл Орлов"
    assert org.post("/users", json={"email": "k2@a.ru", "first_name": "Кирилл", "role": "viewer", "password": "password-1"}).status_code == 422
    p = org.patch(f"/users/{uid}", json={"last_name": "Волков"})
    assert p.status_code == 200 and p.json()["name"] == "Кирилл Волков" and p.json()["first_name"] == "Кирилл"
    assert org.patch(f"/users/{uid}", json={"first_name": ""}).status_code == 422


# ------------------------------------------------------------------ профиль
def test_profile_name_email_and_password(org):
    r = org.patch("/auth/profile", json={"first_name": "Мария", "last_name": "Ким"})
    assert r.status_code == 200 and r.json()["name"] == "Мария Ким"
    # смена e-mail: нужен пароль; адрес снова становится неподтверждённым
    assert org.patch("/auth/profile", json={"email": "new@a.ru"}).status_code == 403
    assert org.patch("/auth/profile", json={"email": "new@a.ru", "current_password": "wrong-pass"}).status_code == 403
    r = org.patch("/auth/profile", json={"email": "New@A.ru", "current_password": "password-1"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new@a.ru" and r.json()["email_verified"] is False
    assert org.get("/auth/me").json()["email"] == "new@a.ru"
    # старый адрес освободился, новый занят
    other = org.c.post("/api/v1/auth/register", json={"org_name": "Др", "first_name": "А", "last_name": "Б", "email": "new@a.ru",
                                                      "password": "password-1", "accept_terms": True})
    assert other.status_code == 409
    # пустое имя нельзя; тот же e-mail — не смена
    assert org.patch("/auth/profile", json={"first_name": ""}).status_code == 422
    assert org.patch("/auth/profile", json={"email": "new@a.ru"}).status_code == 200


def test_profile_email_must_be_unique(org):
    org.post("/users", json={"email": "busy@a.ru", "first_name": "А", "last_name": "Б", "role": "viewer", "password": "password-1"})
    r = org.patch("/auth/profile", json={"email": "busy@a.ru", "current_password": "password-1"})
    assert r.status_code == 409


# ------------------------------------------------------------------ роль «Директор»
def test_director_is_read_only_like_viewer(full_org):
    o = full_org
    o.replan_all()
    for path in ("/reports/overview", "/reports/status", "/reports/load", "/reports/day", "/projects", "/workers", "/products"):
        assert o.get(path, "director").status_code == 200, path
    assert o.post("/projects", "director", json={"name": "Нельзя"}).status_code == 403
    assert o.post("/plans/replan", "director", json={}).status_code == 403
    assert o.get("/users", "director").status_code == 403
    assert o.get("/reports/day", "director").json()["can_enter"] is False
    me = o.get("/auth/me", "director").json()
    assert me["role"] == "director" and me["permissions"] == ["reports"]


# ------------------------------------------------------------------ загрузка пользователей
def row(email, first="Иван", last="Иванов", role="Наблюдатель", **kw):
    return {"email": email, "first_name": first, "last_name": last, "role": role, **kw}


def test_import_users_ok_with_russian_roles_and_links(full_org):
    o = full_org
    wc, w = o.wcs[0]["name"], o.w1["name"]
    rows = [row("a1@x.ru", role="Администратор", password="password-1"),
            row("a2@x.ru", role="Руководитель проекта", password="password-1"),
            row("a3@x.ru", role="директор", password="password-1"),
            row("a4@x.ru", role="Бригадир", work_center=wc.upper(), password="password-1"),
            row("a5@x.ru", role="Сборщик", worker=w, password="password-1"),
            row("a6@x.ru", role="viewer", password="password-1")]
    dry = o.post("/users/import", json={"rows": rows, "dry_run": True}).json()
    assert dry["errors"] == [] and dry["would_create"] == 6
    assert not any(u["email"] == "a1@x.ru" for u in o.get("/users").json())
    r = o.post("/users/import", json={"rows": rows})
    assert r.status_code == 200 and r.json()["created"] == 6, r.text
    users = {u["email"]: u for u in o.get("/users").json()}
    assert users["a1@x.ru"]["role"] == "owner" and users["a2@x.ru"]["role"] == "manager" and users["a3@x.ru"]["role"] == "director"
    assert users["a4@x.ru"]["work_center_id"] == o.wcs[0]["id"] and users["a5@x.ru"]["worker_id"] == o.w1["id"]
    assert users["a1@x.ru"]["name"] == "Иван Иванов"
    assert o.c.post("/api/v1/auth/login", json={"email": "a3@x.ru", "password": "password-1"}).status_code == 200


def test_import_users_is_all_or_nothing_and_reports_rows(full_org):
    o = full_org
    rows = [row("ok@x.ru", password="password-1"),
            row("not-an-email", password="password-1"),
            row("m@a.ru", password="password-1"),                      # уже есть
            row("ok@x.ru", password="password-1"),                     # повтор в файле
            row("r@x.ru", role="Космонавт", password="password-1"),
            row("f@x.ru", role="Бригадир", password="password-1"),     # без участка
            row("s@x.ru", role="Сборщик", worker="Нет такого", password="password-1"),
            row("p@x.ru", password="short"),
            row("n@x.ru", last="", password="password-1")]
    r = o.post("/users/import", json={"rows": rows}).json()
    assert r["created"] == 0
    assert sorted(e["row"] for e in r["errors"]) == [2, 3, 4, 5, 6, 7, 8, 9]
    assert not any(u["email"] == "ok@x.ru" for u in o.get("/users").json())


def test_import_users_invite_needs_no_password(org, monkeypatch):
    r = org.post("/users/import", json={"rows": [row("inv@x.ru")], "send_invite": True})
    assert r.status_code == 200 and r.json()["created"] == 1, r.text
    u = next(x for x in org.get("/users").json() if x["email"] == "inv@x.ru")
    assert u["email_verified"] is False
    assert org.post("/users/import", json={"rows": []}).status_code == 422


# ------------------------------------------------------------------ загрузка каталога
def crow(product, op, norm="10", **kw):
    return {"product": product, "operation": op, "norm_per_day": norm, **kw}


def test_import_catalog_creates_products_versions_and_operations(org):
    org.patch("/settings", json={"qualifications": ["Ученик", "Мастер", "Эксперт"]})
    rows = [crow("Стол", "Раскрой", "12,5", unit="шт", sequence="1", min_skill="Мастер", per_item="2"),
            crow("Стол", "Сборка", "4", sequence="2", min_skill="2"),
            crow("Стул", "Покраска", "8")]
    dry = org.post("/products/import", json={"rows": rows, "dry_run": True}).json()
    assert dry["errors"] == [] and dry["created"] == 2 and not org.get("/products").json()["items"]
    r = org.post("/products/import", json={"rows": rows}).json()
    assert r["created"] == 2 and r["errors"] == []
    prods = {p["name"]: p for p in org.get("/products").json()["items"]}
    assert set(prods) == {"Стол", "Стул"}
    rt = org.get(f"/products/{prods['Стол']['id']}/routings").json()[0]
    assert rt["version_no"] == 1 and rt["is_current"]
    ops = {x["name"]: x for x in rt["operations"]}
    assert ops["Раскрой"]["norm_per_day"] == 12.5 and ops["Раскрой"]["min_skill"] == 2 and ops["Раскрой"]["per_item"] == 2
    assert ops["Сборка"]["sequence"] == 2
    stul = org.get(f"/products/{prods['Стул']['id']}/routings").json()[0]["operations"][0]
    assert stul["sequence"] == 1 and stul["per_item"] == 1 and stul["min_skill"] == 1


def test_import_catalog_existing_product_skip_or_new_version(org):
    org.post("/products/import", json={"rows": [crow("Шкаф", "Резка")]})
    again = org.post("/products/import", json={"rows": [crow("шкаф", "Резка", "20"), crow("Полка", "Резка")]}).json()
    assert again["created"] == 1 and again["skipped"] == ["Шкаф"]
    prods = {p["name"]: p["id"] for p in org.get("/products").json()["items"]}
    assert len(org.get(f"/products/{prods['Шкаф']}/routings").json()) == 1
    v = org.post("/products/import", json={"rows": [crow("Шкаф", "Резка", "20", version_name="Новая")], "on_existing": "version"}).json()
    assert v["versions"] == 1 and v["created"] == 0
    rts = org.get(f"/products/{prods['Шкаф']}/routings").json()
    assert [r["version_no"] for r in rts] == [2, 1] and rts[0]["is_current"] and not rts[1]["is_current"] and rts[0]["name"] == "Новая"


def test_import_catalog_validation_is_all_or_nothing(org):
    rows = [crow("Ок", "Оп"), crow("", "Оп"), crow("Х", ""), crow("Х", "Оп", "0"), crow("Х", "Оп", "abc"),
            crow("Х", "Оп", "5", min_skill="9"), crow("Y", "Оп"), crow("Y", "оп")]
    r = org.post("/products/import", json={"rows": rows}).json()
    assert r["created"] == 0 and sorted(e["row"] for e in r["errors"]) == [2, 3, 4, 5, 6, 8]
    assert org.get("/products").json()["items"] == []


# ------------------------------------------------------------------ закрепление дня и «Загрузка»
def test_pin_endpoint_validates(full_org):
    o = full_org
    assert o.post("/plan-day/pins", json={"worker_id": o.w1["id"], "plan_date": "2099-01-01"}).status_code == 409    # плана ещё нет
    o.replan_all()
    assert o.post("/plan-day/pins", json={"worker_id": o.w1["id"], "plan_date": "2020-01-01"}).status_code == 422
    assert o.post("/plan-day/pins", json={"worker_id": 999999, "plan_date": TODAY.isoformat()}).status_code == 404
    other = next(w for w in o.workers if w["work_center_id"] != o.w1["work_center_id"])
    assert o.post("/plan-day/pins", "foreman", json={"worker_id": other["id"], "plan_date": "2099-01-01"}).status_code == 403
    assert o.post("/plan-day/pins", "foreman", json={"worker_id": o.w1["id"], "plan_date": (TODAY + timedelta(days=3)).isoformat()}).status_code == 201


def test_load_report_has_center_names(full_org):
    o = full_org
    o.replan_all()
    L = o.get("/reports/load").json()
    assert L["centers"] and all(isinstance(v, str) for v in L["centers"].values())
    for n in L["workers"]:
        assert str(L["worker_info"][n]["work_center_id"]) in L["centers"]
    F = o.get("/reports/load", "foreman").json()
    assert set(F["workers"]) <= set(L["workers"]) and F["centers"]


# ------------------------------------------------------------------ дашборд
def test_dashboard_kpi_and_month_orders(full_org):
    o = full_org
    o.replan_all()
    d = o.get("/reports/overview").json()
    k = d["kpi"]
    rows = o.get("/reports/status").json()["rows"]
    open_projects = {r["project"] for r in rows if r["status"] not in ("Готово", "Нет операций")}
    assert k["projects_in_work"] + k["projects_queued"] == len(open_projects)
    assert k["projects_in_work"] == 0                                    # факта ещё нет
    assert k["projects_at_risk"] == len({r["project"] for r in rows if r["status"] == "Риск опоздания"})
    assert k["workers_total"] == len(o.workers) and 0 <= k["workers_with_tasks"] <= k["workers_total"]
    assert k["load_3m"] is not None and k["load_3m"] > 0
    # работники с заданиями на сегодня — ровно те, у кого есть строка плана на сегодня
    day = o.get("/reports/day", params={"day": TODAY.isoformat()}).json()
    assert k["workers_with_tasks"] == len([w for w in day["workers"] if any(t["plan_qty"] for t in w["tasks"]) or w["extras"]])
    # единая таблица: только заказы со сроком в ближайший месяц (и просроченные незавершённые)
    end = (TODAY + timedelta(days=30)).isoformat()
    assert all(r["due"] <= end for r in d["orders_month"])
    assert [r["due"] for r in d["orders_month"]] == sorted(r["due"] for r in d["orders_month"])
    expect = {r["order_line_id"] for r in rows if r["due"] and r["due"] <= end and not (r["due"] < TODAY.isoformat() and r["status"] == "Готово")}
    assert {r["order_line_id"] for r in d["orders_month"]} == expect


def test_dashboard_kpi_empty_org(org):
    k = org.get("/reports/overview").json()["kpi"]
    assert k["projects_in_work"] == k["projects_queued"] == k["projects_at_risk"] == k["workers_total"] == 0 and k["load_3m"] is None


def test_dashboard_foreman_sees_own_section_only(full_org):
    o = full_org
    o.replan_all()
    k = o.get("/reports/overview", "foreman").json()["kpi"]
    in_center = [w for w in o.workers if w["work_center_id"] == o.wcs[0]["id"]]
    assert k["workers_total"] == len(in_center)


# ------------------------------------------------------------------ человеко-смены
def test_person_shifts_left_is_remaining_over_daily_capacity(full_org):
    from test_stage5 import new_order  # noqa: F401  (проверяем на готовом демо-заказе)
    o = full_org
    o.replan_all()
    eff = o.get("/settings").json()["efficiency"]
    oid = o.get("/reports/status").json()["rows"][0]["order_line_id"]
    st = o.get(f"/reports/order-status?order_line_id={oid}").json()
    seen = 0
    for x in st["operations"]:
        assert "person_shifts_left" in x and "shifts_left" in x
        if x["status"] == "Готово":
            assert x["person_shifts_left"] == 0
            continue
        assert x["person_shifts_left"] > 0
        seen += 1
        # не меньше «одного работника с производительностью 10 на всю норму» и не больше разумного верхнего предела
        assert x["person_shifts_left"] == pytest.approx(x["remaining"] / (x["norm_per_day"] * eff * _perf(o, x)), abs=0.06)
    assert seen


def _perf(o, op):
    """Средняя производительность работников операции (как в расчёте); работников нет — 1."""
    names = op["workers"]
    perfs = [w["perf"] for w in o.workers if w["name"] in names]
    if perfs:
        return sum(perfs) / len(perfs)
    cand = [w["perf"] for w in o.workers if w["status"] == "available" and w["auto_plan"] and w["skill"] >= op["min_skill"]
            and (not o.wcs or w["work_center_id"] is not None)]
    return sum(cand) / len(cand) if cand else 1.0
