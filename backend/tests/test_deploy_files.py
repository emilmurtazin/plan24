"""Файлы развёртывания: корневой docker-compose.yml обязан соответствовать условиям App Platform Timeweb."""
import importlib.util
import os
import re
import subprocess

import pytest
import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
spec = importlib.util.spec_from_file_location("check_apps", os.path.join(ROOT, "deploy", "timeweb", "check-apps-compose.py"))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
COMPOSE = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf8").read()


def test_root_compose_satisfies_app_platform_rules():
    assert checker.check(COMPOSE) == []


def test_web_is_first_service_and_only_it_publishes_a_port():
    d = yaml.safe_load(COMPOSE)
    assert next(iter(d["services"])) == "web"
    assert [n for n, s in d["services"].items() if s.get("ports")] == ["web"]


def test_only_migrate_service_gets_owner_credentials():
    """Пароль владельца БД (обходит RLS) не должен попадать в процессы, обслуживающие запросы."""
    d = yaml.safe_load(COMPOSE)
    holders = [n for n, s in d["services"].items() if "PLAN24_DB_MIGRATE_URL" in (s.get("environment") or {})]
    assert holders == ["migrate"]
    for n in ("web", "scheduler", "tg"):
        assert d["services"][n]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("snippet,expect", [
    ("    volumes: ['./x:/x']", "volumes"), ("    privileged: true", "privileged"), ("    network_mode: host", "network_mode"),
    ("    extra_hosts: ['a:1.2.3.4']", "extra_hosts"), ("    extends: {service: web}", "extends"),
])
def test_linter_catches_each_forbidden_directive(snippet, expect):
    bad = COMPOSE.replace("\n  migrate:", f"\n  migrate:\n{snippet}", 1)          # именно ключ сервиса, не depends_on
    assert yaml.safe_load(bad)                                                           # тест сам не ломает YAML
    assert any(expect in e for e in checker.check(bad))


def test_linter_catches_reserved_ports_wrong_first_service_and_top_level_name():
    assert any("зарезервирован" in e for e in checker.check(COMPOSE.replace('ports: ["8000:8000"]', 'ports: ["443:8000"]')))
    d = yaml.safe_load(COMPOSE)
    d["services"] = {k: d["services"][k] for k in ("migrate", "web", "scheduler", "tg")}          # первым стал сервис без порта
    assert any("первый сервис" in e for e in checker.check(yaml.safe_dump(d)))
    assert any("name" in e for e in checker.check("name: plan24\n" + COMPOSE))


def test_every_variable_used_by_compose_is_documented_for_the_panel():
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE))
    example = open(os.path.join(ROOT, "deploy", "timeweb", "apps.env.example"), encoding="utf8").read()
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.M))
    assert used <= documented, used - documented
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", COMPOSE))
    assert {"PLAN24_SECRET", "PLAN24_DB_URL", "PLAN24_DB_MIGRATE_URL", "PLAN24_BASE_URL"} <= required   # без них compose не запустится


def test_dockerfile_matches_compose_build_context():
    df = open(os.path.join(ROOT, "deploy", "Dockerfile"), encoding="utf8").read()
    for src in re.findall(r"^COPY (\S+) ", df, re.M):
        assert os.path.exists(os.path.join(ROOT, src)), f"COPY {src}: нет в репозитории (сборка упала бы)"
    assert "EXPOSE 8000" in df and "USER plan24" in df                                                # порт для платформы; не root


def test_no_secrets_committed_in_deploy_files():
    for dirpath, _, files in os.walk(os.path.join(ROOT, "deploy")):
        for f in files:
            if f.endswith((".yml", ".example", ".sh", ".sql", ".md", "Caddyfile")):
                txt = open(os.path.join(dirpath, f), encoding="utf8").read()
                assert not re.search(r"(?m)^(PLAN24_SECRET|PLAN24_SMTP_PASSWORD|PLAN24_TG_TOKEN)=\S+", txt), f"{f}: похоже на реальный секрет"
