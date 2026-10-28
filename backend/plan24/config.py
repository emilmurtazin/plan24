"""Конфигурация из переменных окружения."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("PLAN24_DATA", Path(__file__).resolve().parent.parent / "data"))


def env() -> str:
    """dev | prod. По умолчанию prod: отладочные возможности (подмена даты, просмотр исходящей почты) включаются явно."""
    return os.environ.get("PLAN24_ENV", "prod")


ACCESS_TTL_MIN = 15
REFRESH_TTL_DAYS = 14
TRIAL_DAYS = 14
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_SEC = 15 * 60


def db_url() -> str:
    url = os.environ.get("PLAN24_DB_URL")
    if url:
        return url
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'plan24.sqlite3'}"


def admin_db_url() -> str | None:
    """Служебное подключение с обходом RLS (только тесты и обслуживание). В боевом приложении не используется."""
    return os.environ.get("PLAN24_DB_ADMIN_URL")


def migrate_db_url() -> str:
    """Миграции выполняются владельцем схемы, а не ролью приложения."""
    return os.environ.get("PLAN24_DB_MIGRATE_URL") or db_url()


def pool_settings() -> dict:
    return {"pool_size": int(os.environ.get("PLAN24_DB_POOL", "10")),
            "max_overflow": int(os.environ.get("PLAN24_DB_OVERFLOW", "10")),
            "pool_recycle": int(os.environ.get("PLAN24_DB_RECYCLE", "1800"))}


def secret_key() -> str:
    """JWT-секрет: переменная окружения; в dev — генерируется и сохраняется в data/.secret."""
    s = os.environ.get("PLAN24_SECRET")
    if s:
        return s
    if env() == "prod":
        raise RuntimeError("PLAN24_SECRET обязателен в prod (в dev он создаётся автоматически)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / ".secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


# ---- почта ----
def base_url() -> str:
    return os.environ.get("PLAN24_BASE_URL", "http://localhost:8000").rstrip("/")


def mail_settings() -> dict:
    """Backend: smtp (если задан PLAN24_SMTP_HOST), иначе console (файлы в data/outbox; только для разработки)."""
    host = os.environ.get("PLAN24_SMTP_HOST")
    return {
        "backend": os.environ.get("PLAN24_MAIL_BACKEND") or ("smtp" if host else "console"),
        "host": host, "port": int(os.environ.get("PLAN24_SMTP_PORT", "587")),
        "user": os.environ.get("PLAN24_SMTP_USER"), "password": os.environ.get("PLAN24_SMTP_PASSWORD"),
        "from": os.environ.get("PLAN24_MAIL_FROM", "Plan 24 <noreply@localhost>"),
        "security": os.environ.get("PLAN24_SMTP_SECURITY", "starttls"),      # starttls | ssl | none
        "timeout": float(os.environ.get("PLAN24_SMTP_TIMEOUT", "15")),
    }


EMAIL_TOKEN_TTL = {"verify": 3 * 24 * 3600, "reset": 3600, "invite": 7 * 24 * 3600}


# ---- Telegram ----
def tg_relay_ca() -> "str | None":
    """Сертификат релея: PLAN24_TG_CA_PEM (PEM — в том числе с «\\n» вместо переводов строк — или base64 от PEM одной строкой)
    либо PLAN24_TG_CA_FILE (путь к файлу)."""
    import base64
    raw = (os.environ.get("PLAN24_TG_CA_PEM") or "").strip()
    if not raw and os.environ.get("PLAN24_TG_CA_FILE"):
        with open(os.environ["PLAN24_TG_CA_FILE"], encoding="ascii") as f:
            raw = f.read().strip()
    if not raw:
        return None
    raw = raw.replace("\\n", "\n")
    if "BEGIN CERTIFICATE" not in raw:
        try:
            raw = base64.b64decode("".join(raw.split()), validate=True).decode("ascii")
        except Exception as e:                                               # noqa: BLE001
            raise ValueError("PLAN24_TG_CA_PEM: ожидается сертификат в формате PEM или его base64 в одну строку") from e
    if "BEGIN CERTIFICATE" not in raw:
        raise ValueError("PLAN24_TG_CA_PEM: в значении нет сертификата (BEGIN CERTIFICATE)")
    return raw


def telegram_settings() -> dict:
    """Бот включается, когда заданы токен и секрет вебхука. Секрет в URL + отдельный секрет в заголовке (подпись)."""
    import hashlib
    token, secret = os.environ.get("PLAN24_TG_TOKEN"), os.environ.get("PLAN24_TG_SECRET")
    return {
        "enabled": bool(token and secret), "token": token, "secret": secret,
        "header_secret": hashlib.sha256(f"hdr:{secret}".encode()).hexdigest()[:48] if secret else None,
        "bot_username": os.environ.get("PLAN24_TG_BOT_USERNAME", ""),
        # С российских серверов api.telegram.org может быть недоступен: запросы бота можно направить через свой релей
        # (PLAN24_TG_API_BASE=https://relay.example.com) или прокси (PLAN24_TG_PROXY=http://host:port, socks5://host:port)
        "api_base": os.environ.get("PLAN24_TG_API_BASE", "https://api.telegram.org").rstrip("/"),
        "proxy": os.environ.get("PLAN24_TG_PROXY") or None,
        # сертификат релея, которому доверяем (релей по IP-адресу с самоподписанным сертификатом): PEM, тот же PEM в base64
        # одной строкой (удобно для переменных окружения) или путь к файлу. Если задан — других сертификатов клиент не принимает.
        "ca_pem": tg_relay_ca(),
        "morning_at": os.environ.get("PLAN24_TG_MORNING_AT", "07:30"),
        "evening_at": os.environ.get("PLAN24_TG_EVENING_AT", "17:30"),
    }


TG_CODE_TTL = 24 * 3600
TGAPP_TTL_MIN = 480                               # сессия мини-приложения: 8 часов (смена)


def tgapp_max_age() -> int:
    """Насколько свежими должны быть данные запуска из Telegram (сек.)."""
    return int(os.environ.get("PLAN24_TGAPP_MAX_AGE", "86400"))


def tgapp_url() -> "str | None":
    """Адрес мини-приложения для кнопок бота. Telegram принимает только HTTPS: пока PLAN24_BASE_URL не https — кнопок нет.
    Выключить приложение можно PLAN24_TGAPP=off."""
    if os.environ.get("PLAN24_TGAPP", "on").lower() in ("off", "0", "no") or not telegram_settings()["enabled"]:
        return None
    base = base_url()
    return base + "/tg/" if base.startswith("https://") else None


def metrika_id() -> str:
    """Номер счётчика Яндекс.Метрики для лендинга (PLAN24_METRIKA_ID, только цифры); пусто — аналитика не подключается."""
    v = (os.environ.get("PLAN24_METRIKA_ID") or "").strip()
    return v if v.isdigit() else ""
