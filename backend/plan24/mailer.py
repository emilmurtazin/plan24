"""Отправка писем. Backend выбирается настройками: smtp | console (файлы в data/outbox, для разработки) | memory (тесты).

Отправка не должна ронять запрос и не должна писать адреса и тексты писем в логи (ТЗ 12, «Минимизация»):
при сбое в лог попадает только тип ошибки и домен получателя.
"""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import config

log = logging.getLogger("plan24.mail")
OUTBOX: deque = deque(maxlen=200)          # последние письма (memory/console): для тестов и dev-просмотра
_lock = threading.Lock()


def build_message(to: str, subject: str, text: str, html_body: str | None, sender: str) -> EmailMessage:
    m = EmailMessage()
    subject = " ".join(subject.split())              # переводы строк в теме = инъекция заголовков
    m["From"], m["To"], m["Subject"] = sender, to, subject
    m["Date"], m["Message-ID"] = formatdate(localtime=False), make_msgid(domain="plan24")
    m.set_content(text)
    if html_body:
        m.add_alternative(html_body, subtype="html")
    return m


def send(to: str, subject: str, text: str, html_body: str | None = None) -> bool:
    """Возвращает True при успехе. Исключений не бросает."""
    cfg = config.mail_settings()
    try:
        msg = build_message(to, subject, text, html_body, cfg["from"])
        backend = cfg["backend"]
        if backend == "smtp":
            _smtp(msg, cfg)
        else:
            with _lock:
                OUTBOX.append({"to": to, "subject": subject, "text": text, "html": html_body, "at": time.time()})
            if backend == "console":
                config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                d = config.DATA_DIR / "outbox"
                d.mkdir(exist_ok=True)
                (d / f"{int(time.time() * 1000)}.eml").write_bytes(bytes(msg))
        return True
    except Exception as e:                                   # noqa: BLE001 — сбой почты не должен ломать запрос
        log.error("mail send failed: %s (recipient domain: %s)", type(e).__name__, to.rsplit("@", 1)[-1])
        return False


def _smtp(msg: EmailMessage, cfg: dict) -> None:
    host, port, sec = cfg["host"], cfg["port"], cfg["security"]
    if not host:
        raise RuntimeError("PLAN24_SMTP_HOST не задан")
    ctx = ssl.create_default_context()
    if sec == "ssl":
        srv = smtplib.SMTP_SSL(host, port, timeout=cfg["timeout"], context=ctx)
    else:
        srv = smtplib.SMTP(host, port, timeout=cfg["timeout"])
    with srv:
        srv.ehlo()
        if sec == "starttls":
            srv.starttls(context=ctx)
            srv.ehlo()
        if cfg["user"]:
            srv.login(cfg["user"], cfg["password"] or "")
        srv.send_message(msg)


# ------------------------------------------------------------------ шаблоны (русский, простой текст + HTML)
def _wrap(title: str, body_html: str) -> str:
    return (f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;color:#14212b">'
            f'<h2 style="margin:0 0 12px">{html.escape(title)}</h2>{body_html}'
            f'<p style="color:#5b6b78;font-size:13px;margin-top:24px">Plan 24 — планирование производства. '
            f'Если вы не ожидали это письмо, просто проигнорируйте его.</p></div>')


def _button(url: str, label: str) -> str:
    u = html.escape(url, quote=True)
    return (f'<p><a href="{u}" style="background:#1f5fbf;color:#fff;padding:12px 20px;border-radius:6px;'
            f'text-decoration:none;display:inline-block">{html.escape(label)}</a></p>'
            f'<p style="font-size:13px;color:#5b6b78">Если кнопка не работает, откройте ссылку:<br>{u}</p>')


def link(kind: str, token: str) -> str:
    return f"{config.base_url()}/#/{kind}?token={token}"


def verify_email(name: str, org: str, token: str) -> tuple[str, str, str]:
    url = link("verify", token)
    text = (f"Здравствуйте, {name}!\n\nВы зарегистрировали организацию «{org}» в Plan 24.\n"
            f"Подтвердите e-mail по ссылке (действует 3 суток):\n{url}\n")
    body = (f"<p>Здравствуйте, {html.escape(name)}!</p><p>Вы зарегистрировали организацию «{html.escape(org)}» в Plan 24. "
            f"Подтвердите e-mail (ссылка действует 3 суток).</p>{_button(url, 'Подтвердить e-mail')}")
    return "Подтвердите e-mail в Plan 24", text, _wrap("Подтвердите e-mail", body)


def reset_password(name: str, token: str) -> tuple[str, str, str]:
    url = link("reset", token)
    text = (f"Здравствуйте, {name}!\n\nМы получили запрос на смену пароля в Plan 24.\n"
            f"Задайте новый пароль по ссылке (действует 1 час, работает один раз):\n{url}\n\n"
            f"Если вы не запрашивали смену пароля — ничего не делайте, пароль не изменится.\n")
    body = (f"<p>Здравствуйте, {html.escape(name)}!</p><p>Мы получили запрос на смену пароля. Ссылка действует 1 час и "
            f"работает один раз.</p>{_button(url, 'Задать новый пароль')}"
            f"<p>Если вы не запрашивали смену пароля — ничего не делайте, пароль не изменится.</p>")
    return "Смена пароля в Plan 24", text, _wrap("Смена пароля", body)


def invite(name: str, org: str, inviter: str, role_ru: str, token: str) -> tuple[str, str, str]:
    url = link("invite", token)
    text = (f"Здравствуйте, {name}!\n\n{inviter} приглашает вас в организацию «{org}» в Plan 24 (роль: {role_ru}).\n"
            f"Задайте пароль и войдите (ссылка действует 7 суток):\n{url}\n")
    body = (f"<p>Здравствуйте, {html.escape(name)}!</p><p>{html.escape(inviter)} приглашает вас в организацию "
            f"«{html.escape(org)}» в Plan 24 (роль: {html.escape(role_ru)}).</p>{_button(url, 'Задать пароль и войти')}")
    return f"Приглашение в «{org}» — Plan 24", text, _wrap("Приглашение", body)
