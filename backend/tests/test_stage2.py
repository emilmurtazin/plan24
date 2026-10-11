"""Этап 2 доработок: названия версий, переименование операций, уровни квалификации организации."""


def _used_routing(o):
    for p in o.get("/products").json()["items"]:
        for r in o.get(f"/products/{p['id']}/routings").json():
            if r["used"]:
                return p, r
    raise AssertionError("в демо нет используемого маршрута")


def test_routing_name_rename_and_named_new_version(full_org):
    o = full_org
    p, r = _used_routing(o)
    assert r["name"] == ""
    # переименовать можно и версию, уже стоящую в заказах; технолог может, наблюдатель и бригадир — нет
    x = o.patch(f"/routings/{r['id']}", "technologist", json={"name": "  Базовая  "})
    assert x.status_code == 200 and x.json()["name"] == "Базовая" and x.json()["used"] is True
    assert o.patch(f"/routings/{r['id']}", "viewer", json={"name": "x"}).status_code == 403
    assert o.patch(f"/routings/{r['id']}", "foreman", json={"name": "x"}).status_code == 403
    assert o.patch("/routings/999999", "technologist", json={"name": "x"}).status_code == 404
    # новая версия сразу с названием
    n = o.post(f"/products/{p['id']}/routings", "technologist", params={"name": "Усиленная"})
    assert n.status_code == 201 and n.json()["name"] == "Усиленная" and n.json()["version_no"] == r["version_no"] + 1
    lst = {i["id"]: i for i in o.get("/routings", "viewer").json()["items"]}
    assert lst[r["id"]]["name"] == "Базовая" and lst[n.json()["id"]]["name"] == "Усиленная"
    assert any(a["entity"] == "routing" and a["action"] == "update" for a in o.get("/audit").json()["items"])


def test_operation_rename_allowed_but_norm_locked_when_used(full_org):
    o = full_org
    _, r = _used_routing(o)
    op = r["operations"][0]
    ok = o.patch(f"/operations/{op['id']}", "technologist", json={"name": "Новое название"})
    assert ok.status_code == 200 and ok.json()["name"] == "Новое название"
    locked = o.patch(f"/operations/{op['id']}", "technologist", json={"norm_per_day": op["norm_per_day"] + 1})
    assert locked.status_code == 409 and "название" in locked.json()["title"]
    other = r["operations"][1]
    dup = o.patch(f"/operations/{other['id']}", "technologist", json={"name": "Новое название"})
    assert dup.status_code == 409           # повтор названия внутри версии


def test_qualification_levels_settings_and_checks(full_org):
    o = full_org
    st = o.get("/settings", "technologist").json()                       # технолог видит названия уровней
    n0 = len(st["qualifications"])
    assert n0 >= 3 and st["qualifications"][0] == "Уровень 1"
    top = max(w["skill"] for w in o.get("/workers").json()["items"])
    assert n0 >= top
    # переименовать и добавить уровни
    names = ["Ученик", "Сборщик", "Мастер", "Наладчик", "Бригадир-мастер"]
    r = o.patch("/settings", json={"qualifications": names})
    assert r.status_code == 200 and r.json()["qualifications"] == names
    assert o.patch("/settings", "technologist", json={"qualifications": names}).status_code == 403
    # некорректные списки
    assert o.patch("/settings", json={"qualifications": ["А", "а"]}).status_code == 422      # повтор
    assert o.patch("/settings", json={"qualifications": ["А", " "]}).status_code == 422      # пустое
    assert o.patch("/settings", json={"qualifications": []}).status_code == 422
    assert o.patch("/settings", json={"qualifications": [str(i) for i in range(11)]}).status_code == 422
    # уровень 5 можно назначить, 6 — нет
    w = o.get("/workers").json()["items"][0]
    assert o.patch(f"/workers/{w['id']}", json={"skill": 5, "version": w["version"]}).status_code == 200
    assert o.patch(f"/workers/{w['id']}", json={"skill": 6}).status_code == 422
    assert o.post("/workers", json={"name": "Новичок-уровень", "skill": 7}).status_code == 422
    # нельзя сократить уровни ниже используемого
    r = o.patch("/settings", json={"qualifications": names[:3]})
    assert r.status_code == 409
    # операции: требуемая квалификация в пределах числа уровней
    p = o.get("/products").json()["items"][0]
    rid = o.post(f"/products/{p['id']}/routings").json()["id"]
    assert o.post(f"/routings/{rid}/operations", "technologist", json={"name": "Уровень-5", "norm_per_day": 3, "min_skill": 5}).status_code == 201
    assert o.post(f"/routings/{rid}/operations", "technologist", json={"name": "Уровень-6", "norm_per_day": 3, "min_skill": 6}).status_code == 422


def test_new_org_and_demo_cover_used_levels(full_org):
    # уровни не меньше самого высокого, который реально используется загруженными данными
    st = full_org.get("/settings").json()
    used = max([w["skill"] for w in full_org.get("/workers").json()["items"]] or [1])
    assert len(st["qualifications"]) >= used
