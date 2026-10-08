"""Письма: подтверждение e-mail, сброс пароля, приглашения, SMTP-доставка (FR-01)."""
import re
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import Org
from plan24 import db as dbm, mailer
from plan24.models import EmailToken, User, utcnow


def mails(to=None):
    return [m for m in mailer.OUTBOX if to is None or m["to"] == to]


def token_from(mail) -> str:
    m = re.search(r"token=([\w\-]+)", mail["text"])
    assert m, mail["text"]
    return m.group(1)


def test_registration_sends_verification_mail_with_working_link(env):
    o = Org(env, "anna@a.ru", "Цех А")
    ms = mails("anna@a.ru")
    assert len(ms) == 1 and "Подтвердите e-mail" in ms[0]["subject"]
    assert ms[0]["text"].count("https://plan24.test/#/verify?token=") == 1 and "Цех А" in ms[0]["text"]
    assert o.get("/auth/me").json()["email_verified"] is False
    tok = token_from(ms[0])
    assert env.post("/api/v1/auth/verify", json={"token": tok}).status_code == 200
    assert o.get("/auth/me").json()["email_verified"] is True
    assert env.post("/api/v1/auth/verify", json={"token": tok}).status_code == 400       # одноразовая


def test_verify_rejects_garbage_and_expired_tokens(env):
    o = Org(env, "anna@a.ru", "Цех А")
    tok = token_from(mails("anna@a.ru")[0])
    assert env.post("/api/v1/auth/verify", json={"token": "x" * 40}).status_code == 400
    s = dbm.admin_session()
    try:
        t = s.scalars(select(EmailToken)).first()
        t.expires_at = utcnow() - timedelta(seconds=1); s.commit()
    finally:
        s.close()
    assert env.post("/api/v1/auth/verify", json={"token": tok}).status_code == 400


def test_resend_verification_is_rate_limited_and_invalidates_old_link(env):
    o = Org(env, "anna@a.ru", "Цех А")
    old = token_from(mails("anna@a.ru")[0])
    assert o.post("/auth/resend-verification").json() == {"sent": True}
    assert o.post("/auth/resend-verification").status_code == 429                 # не чаще раза в минуту
    new = token_from(mails("anna@a.ru")[-1])
    assert old != new
    assert env.post("/api/v1/auth/verify", json={"token": old}).status_code == 400   # действует только последняя ссылка
    assert env.post("/api/v1/auth/verify", json={"token": new}).status_code == 200
    assert o.post("/auth/resend-verification").json() == {"already_verified": True}


def test_forgot_password_does_not_reveal_whether_email_exists(env):
    Org(env, "anna@a.ru", "Цех А"); mailer.OUTBOX.clear()
    a = env.post("/api/v1/auth/forgot-password", json={"email": "anna@a.ru"})
    b = env.post("/api/v1/auth/forgot-password", json={"email": "nobody@a.ru"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert len(mails("anna@a.ru")) == 1 and not mails("nobody@a.ru")                # письмо — только существующему


def test_forgot_password_rate_limit(env):
    Org(env, "anna@a.ru", "Цех А")
    codes = [env.post("/api/v1/auth/forgot-password", json={"email": "anna@a.ru"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_password_reset_full_flow_closes_old_sessions(env):
    o = Org(env, "anna@a.ru", "Цех А")
    rt = o.data["refresh_token"]; mailer.OUTBOX.clear()
    env.post("/api/v1/auth/forgot-password", json={"email": "anna@a.ru"})
    tok = token_from(mails("anna@a.ru")[0])
    assert "1 час" in mails("anna@a.ru")[0]["text"]
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "short"}).status_code == 422
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "brand-new-1"}).status_code == 200
    assert env.post("/api/v1/auth/login", json={"email": "anna@a.ru", "password": "password-1"}).status_code == 401
    assert env.post("/api/v1/auth/login", json={"email": "anna@a.ru", "password": "brand-new-1"}).status_code == 200
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": rt}).status_code == 401       # прежние сессии закрыты
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "another-1"}).status_code == 400


def test_second_reset_request_invalidates_first_link(env):
    Org(env, "anna@a.ru", "Цех А"); mailer.OUTBOX.clear()
    for _ in range(2):
        env.post("/api/v1/auth/forgot-password", json={"email": "anna@a.ru"})
    first, second = (token_from(m) for m in mails("anna@a.ru"))
    assert env.post("/api/v1/auth/reset-password", json={"token": first, "new_password": "brand-new-1"}).status_code == 400
    assert env.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "brand-new-1"}).status_code == 200


def test_reset_token_of_deactivated_user_is_rejected(env):
    o = Org(env, "anna@a.ru", "Цех А")
    u = o.add_user("manager", "m@a.ru"); mailer.OUTBOX.clear()
    env.post("/api/v1/auth/forgot-password", json={"email": "m@a.ru"})
    tok = token_from(mails("m@a.ru")[0])
    o.patch(f"/users/{u['id']}", json={"is_active": False})
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "brand-new-1"}).status_code == 400
    mailer.OUTBOX.clear()
    env.post("/api/v1/auth/forgot-password", json={"email": "m@a.ru"})
    assert not mails("m@a.ru")                                                     # отключённому письмо не уходит


def test_invite_flow_user_sets_own_password(env):
    o = Org(env, "anna@a.ru", "Цех «Мебель»"); mailer.OUTBOX.clear()
    assert o.post("/users", json={"email": "z@a.ru", "name": "Зинаида Т", "role": "viewer"}).status_code == 422   # ни пароля, ни приглашения
    r = o.post("/users", json={"email": "z@a.ru", "name": "Зинаида Т", "role": "viewer", "send_invite": True})
    assert r.status_code == 201
    m = mails("z@a.ru")[0]
    assert "Цех «Мебель»" in m["subject"] and "наблюдатель" in m["text"] and "https://plan24.test/#/invite?token=" in m["text"]
    assert env.post("/api/v1/auth/login", json={"email": "z@a.ru", "password": "password-1"}).status_code == 401
    tok = token_from(m)
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "my-own-pass-1"}).status_code == 200
    lg = env.post("/api/v1/auth/login", json={"email": "z@a.ru", "password": "my-own-pass-1"})
    assert lg.status_code == 200 and lg.json()["user"]["role"] == "viewer" and lg.json()["user"]["email_verified"] is True
    # повторное приглашение — новая ссылка, прежняя не работает
    assert o.post(f"/users/{r.json()['id']}/invite").json() == {"sent": True}
    assert len(mails("z@a.ru")) == 2


def test_invite_expires_after_7_days(env):
    o = Org(env, "anna@a.ru", "Цех А"); mailer.OUTBOX.clear()
    o.post("/users", json={"email": "z@a.ru", "name": "Зинаида Т", "role": "viewer", "send_invite": True})
    tok = token_from(mails("z@a.ru")[0])
    s = dbm.admin_session()
    try:
        t = s.scalars(select(EmailToken).where(EmailToken.kind == "invite")).first()
        assert 6.9 * 86400 < (t.expires_at - utcnow()).total_seconds() <= 7 * 86400
        t.expires_at = utcnow() - timedelta(seconds=1); s.commit()
    finally:
        s.close()
    assert env.post("/api/v1/auth/reset-password", json={"token": tok, "new_password": "brand-new-1"}).status_code == 400


def test_tokens_are_stored_hashed_only(env):
    Org(env, "anna@a.ru", "Цех А")
    tok = token_from(mails("anna@a.ru")[0])
    s = dbm.admin_session()
    try:
        hashes = [t.token_hash for t in s.scalars(select(EmailToken))]
    finally:
        s.close()
    assert tok not in hashes and all(len(h) == 64 for h in hashes)


def test_html_and_header_injection_in_names_is_neutralised(env):
    Org(env, "evil@a.ru", "Цех <script>alert(1)</script>\r\nBcc: x@evil.ru")
    m = mails("evil@a.ru")[0]
    assert "\n" not in m["subject"] and "\r" not in m["subject"]
    assert "<script>" not in (m["html"] or "") and "&lt;script&gt;" in (m["html"] or "")
    msg = mailer.build_message("a@a.ru", m["subject"], m["text"], m["html"], "x <x@x>")
    assert msg["Bcc"] is None


def test_mail_failure_does_not_break_registration(env, monkeypatch):
    monkeypatch.setenv("PLAN24_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("PLAN24_SMTP_HOST", "127.0.0.1"); monkeypatch.setenv("PLAN24_SMTP_PORT", "1")   # порт закрыт
    monkeypatch.setenv("PLAN24_SMTP_TIMEOUT", "1")
    o = Org(env, "anna@a.ru", "Цех А")
    assert o.get("/auth/me").status_code == 200
    assert mailer.send("a@b.ru", "тема", "текст") is False                                              # без исключения


@pytest.fixture()
def smtp_server():
    from aiosmtpd.controller import Controller
    box = []

    class H:
        async def handle_DATA(self, server, session, envelope):
            box.append(envelope); return "250 OK"
    import socket
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    c = Controller(H(), hostname="127.0.0.1", port=port)
    c.start()
    yield port, box
    c.stop()


def test_real_smtp_delivery_utf8_text_and_html(env, monkeypatch, smtp_server):
    port, box = smtp_server
    monkeypatch.setenv("PLAN24_MAIL_BACKEND", "smtp"); monkeypatch.setenv("PLAN24_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("PLAN24_SMTP_PORT", str(port)); monkeypatch.setenv("PLAN24_SMTP_SECURITY", "none")
    monkeypatch.setenv("PLAN24_MAIL_FROM", "Plan 24 <noreply@plan24.test>")
    Org(env, "anna@a.ru", "Цех Ёлка")
    for _ in range(50):
        if box: break
        time.sleep(0.1)
    assert box, "письмо не дошло до SMTP-сервера"
    import email
    from email import policy
    msg = email.message_from_bytes(box[0].content, policy=policy.default)
    assert msg["To"] == "anna@a.ru" and "plan24.test" in msg["From"] and "Подтвердите" in msg["Subject"]
    assert "Цех Ёлка" in msg.get_body(("plain",)).get_content()
    assert "Подтвердить e-mail" in msg.get_body(("html",)).get_content()
    assert box[0].mail_from == "noreply@plan24.test" and box[0].rcpt_tos == ["anna@a.ru"]


def test_dev_outbox_is_hidden_in_prod(env, monkeypatch):
    Org(env, "anna@a.ru", "Цех А")
    assert env.get("/api/v1/dev/outbox").status_code == 200
    monkeypatch.setenv("PLAN24_ENV", "prod")
    assert env.get("/api/v1/dev/outbox").status_code == 404
