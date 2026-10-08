#!/usr/bin/env python3
"""Сверка docker-compose.yml с условиями App Platform Timeweb Cloud (timeweb.cloud/docs/apps/deploying-with-docker-compose).
Запуск: python3 deploy/timeweb/check-apps-compose.py [docker-compose.yml]. Код выхода 1 — есть нарушения."""
import re
import sys

import yaml

FORBIDDEN_KEYS = {"cap_add", "cgroup", "cgroup_parent", "devices", "privileged", "device_cgroup_rules", "volumes", "ipc", "pid",
                  "security_opt", "userns_mode", "sysctls", "volumes_from", "uts", "extra_hosts", "use_api_socket", "include",
                  "extends", "external", "name", "driver_opts"}


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}/{k}", k, v
            yield from walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def check(text: str) -> list:
    errs = []
    doc = yaml.safe_load(text)
    services = doc.get("services") or {}
    if not services:
        return ["нет секции services"]
    for p, k, v in walk(doc):
        if k in FORBIDDEN_KEYS:
            errs.append(f"запрещённая директива «{k}» ({p})")
        if k == "network_mode" and (v == "host" or str(v).startswith("container:")):
            errs.append(f"запрещён network_mode: {v} ({p})")
    first = next(iter(services))
    if not services[first].get("ports"):
        errs.append(f"первый сервис «{first}» не публикует порт — платформа проксирует домен только на первый сервис")
    for name, svc in services.items():
        for port in svc.get("ports") or []:
            host = str(port).split(":")[0] if ":" in str(port) else str(port)
            if host.split("/")[0] in ("80", "443"):
                errs.append(f"{name}: хост-порт {host} зарезервирован под веб-сервер платформы")
        for dep in (svc.get("depends_on") or {}):
            if dep not in services:
                errs.append(f"{name}: depends_on указывает на несуществующий сервис «{dep}»")
        if "build" in svc and isinstance(svc["build"], dict) and ".." in str(svc["build"].get("context", "")):
            errs.append(f"{name}: контекст сборки выходит за пределы репозитория")
    # переменные, которые ждёт compose, должны быть описаны в примере для панели
    try:
        example = open(__file__.rsplit("/", 1)[0] + "/apps.env.example", encoding="utf8").read()
        used = set(re.findall(r"\$\{([A-Z0-9_]+)", text))
        documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.M))
        for var in sorted(used - documented):
            errs.append(f"переменная {var} используется в compose, но не описана в deploy/timeweb/apps.env.example")
    except FileNotFoundError:
        pass
    return errs


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "docker-compose.yml"
    errors = check(open(path, encoding="utf8").read())
    if errors:
        print("Нарушения условий App Platform:"); [print("  -", e) for e in errors]; sys.exit(1)
    print("docker-compose.yml соответствует условиям App Platform (проверено по документации Timeweb).")
