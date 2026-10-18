"""Связь с Telegram через собственный релей (сервер за пределами РФ): закрепление сертификата релея по IP,
разбор PLAN24_TG_CA_PEM, понятные причины сбоев и команда check."""
import base64
import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from plan24 import config
from plan24.telegram import __main__ as tgmain
from plan24.telegram.client import TelegramClient

TOKEN = "123456:TESTTOKEN"
needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="нужен openssl")


def make_cert(tmp_path, ip="127.0.0.1"):
    crt, key = tmp_path / "relay.crt", tmp_path / "relay.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
                    "-keyout", str(key), "-out", str(crt), "-subj", "/CN=plan24-telegram-relay",
                    "-addext", f"subjectAltName=IP:{ip}", "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,digitalSignature,keyCertSign", "-addext", "extendedKeyUsage=serverAuth"],
                   check=True, capture_output=True)
    return crt, key


class Relay:
    """Локальный HTTPS-«релей»: отвечает как Bot API, ответ 502 с HTML — как nginx, который не достал до Telegram."""
    def __init__(self, crt, key, broken=False):
        outer = self
        self.seen = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.seen.append((self.path, body))
                if broken:
                    out, code, ct = b"<html><h1>502 Bad Gateway</h1></html>", 502, "text/html"
                elif self.path.startswith(f"/bot{TOKEN}/getMe"):
                    out, code, ct = json.dumps({"ok": True, "result": {"username": "plan24_bot"}}).encode(), 200, "application/json"
                elif self.path.startswith(f"/bot{TOKEN}/"):
                    out, code, ct = json.dumps({"ok": True, "result": {"ok": 1}}).encode(), 200, "application/json"
                else:
                    out, code, ct = json.dumps({"ok": False, "error_code": 404}).encode(), 404, "application/json"
                self.send_response(code); self.send_header("content-type", ct); self.send_header("content-length", str(len(out))); self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.base = f"https://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


# ------------------------------------------------------------------ PLAN24_TG_CA_PEM
PEM = "-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----"


def test_ca_from_pem_base64_escaped_newlines_and_file(monkeypatch, tmp_path):
    for k in ("PLAN24_TG_CA_PEM", "PLAN24_TG_CA_FILE"):
        monkeypatch.delenv(k, raising=False)
    assert config.tg_relay_ca() is None and config.telegram_settings()["ca_pem"] is None
    monkeypatch.setenv("PLAN24_TG_CA_PEM", PEM)
    assert config.tg_relay_ca() == PEM
    monkeypatch.setenv("PLAN24_TG_CA_PEM", base64.b64encode(PEM.encode()).decode())
    assert config.tg_relay_ca() == PEM
    monkeypatch.setenv("PLAN24_TG_CA_PEM", PEM.replace("\n", "\\n"))              # переводы строк, записанные как «\n»
    assert config.tg_relay_ca() == PEM
    f = tmp_path / "ca.pem"
    f.write_text(PEM)
    monkeypatch.delenv("PLAN24_TG_CA_PEM")
    monkeypatch.setenv("PLAN24_TG_CA_FILE", str(f))
    assert config.tg_relay_ca() == PEM


@pytest.mark.parametrize("bad", ["not a cert", "%%%%", base64.b64encode(b"hello").decode()])
def test_ca_rejects_garbage(monkeypatch, bad):
    monkeypatch.delenv("PLAN24_TG_CA_FILE", raising=False)
    monkeypatch.setenv("PLAN24_TG_CA_PEM", bad)
    with pytest.raises(ValueError):
        config.tg_relay_ca()


# ------------------------------------------------------------------ настоящий TLS: закрепление сертификата релея
@needs_openssl
def test_client_talks_to_relay_only_with_pinned_certificate(tmp_path):
    crt, key = make_cert(tmp_path)
    r = Relay(crt, key)
    try:
        pem = crt.read_text()
        c = TelegramClient(TOKEN, api_base=r.base, ca_pem=pem)
        assert c.get_me()["username"] == "plan24_bot"
        c.send_message(7, "задание на сегодня")
        assert r.seen[-1][0] == f"/bot{TOKEN}/sendMessage" and r.seen[-1][1]["text"] == "задание на сегодня"
        # без закреплённого сертификата самоподписанный релей не принимается
        with pytest.raises(httpx.ConnectError):
            TelegramClient(TOKEN, api_base=r.base).get_me()
        # чужой сертификат тоже не подходит — подмена релея не пройдёт
        other = tmp_path / "other"
        other.mkdir()
        crt2, _ = make_cert(other)
        with pytest.raises(httpx.ConnectError):
            TelegramClient(TOKEN, api_base=r.base, ca_pem=crt2.read_text()).get_me()
        # строгая проверка (по умолчанию в Python 3.13+) тоже проходит
        ctx = ssl.create_default_context(cadata=pem)
        ctx.verify_flags |= ssl.VERIFY_X509_STRICT
        assert httpx.get(r.base + "/x", verify=ctx).status_code in (200, 501)
    finally:
        r.close()


@needs_openssl
def test_certificate_must_match_relay_address(tmp_path):
    crt, key = make_cert(tmp_path, ip="10.9.9.9")                       # сертификат выпущен на другой адрес
    r = Relay(crt, key)
    try:
        with pytest.raises(httpx.ConnectError):
            TelegramClient(TOKEN, api_base=r.base, ca_pem=crt.read_text()).get_me()
    finally:
        r.close()


@needs_openssl
def test_relay_returning_html_gives_readable_error(tmp_path):
    crt, key = make_cert(tmp_path)
    r = Relay(crt, key, broken=True)
    try:
        with pytest.raises(RuntimeError) as e:
            TelegramClient(TOKEN, api_base=r.base, ca_pem=crt.read_text()).get_me()
        assert "502" in str(e.value) and TOKEN not in str(e.value)       # токен в сообщения об ошибках не попадает
        assert "relay-check.sh" in tgmain.explain(e.value)
    finally:
        r.close()


# ------------------------------------------------------------------ диагностика
def cfg(**kw):
    return {"api_base": "https://72.56.68.43:9443", "proxy": None, "ca_pem": PEM, "bot_username": "plan24_bot", **kw}


def test_route_text_hides_credentials():
    assert "72.56.68.43" in tgmain.route_text(cfg()) and "закреплён" in tgmain.route_text(cfg())
    t = tgmain.route_text(cfg(api_base="https://api.telegram.org", ca_pem=None, proxy="socks5://user:secret@10.0.0.1:1080"))
    assert "10.0.0.1" in t and "secret" not in t and "user" not in t
    assert tgmain.route_text(cfg(api_base="https://api.telegram.org", ca_pem=None)) == "напрямую"


def test_explain_gives_actionable_hints():
    assert "PLAN24_TG_CA_PEM" in tgmain.explain(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"))
    assert "порт релея" in tgmain.explain(httpx.ConnectTimeout("t"))
    assert "PLAN24_TG_API_BASE" in tgmain.explain(httpx.ConnectError("refused"))
    assert "PLAN24_TG_TOKEN" in tgmain.explain(RuntimeError("telegram getMe: 401"))
    assert "токен" in tgmain.explain(RuntimeError("telegram getMe: 404"))


class FakeTG:
    def __init__(self, fail=None, username="plan24_bot", url=""):
        self.fail, self.username, self.url = fail, username, url

    def get_me(self):
        if self.fail:
            raise self.fail
        return {"username": self.username}

    def get_webhook_info(self):
        return {"url": self.url, "pending_update_count": 3, "last_error_message": ""}


def test_probe_reports_ok_wrong_bot_and_failure(capsys):
    assert tgmain.probe(FakeTG(), cfg(), with_webhook=True) is True
    out = capsys.readouterr().out
    assert "@plan24_bot" in out and "не установлен" in out
    assert tgmain.probe(FakeTG(username="other_bot"), cfg()) is True
    assert "PLAN24_TG_BOT_USERNAME" in capsys.readouterr().out
    assert tgmain.probe(FakeTG(fail=httpx.ConnectTimeout("t")), cfg()) is False
    assert "недоступен" in capsys.readouterr().out


def test_check_command_exit_codes(monkeypatch, capsys):
    from plan24.telegram import client as tgclient
    monkeypatch.setenv("PLAN24_TG_TOKEN", TOKEN)
    monkeypatch.setenv("PLAN24_TG_SECRET", "s")
    try:
        tgclient.set_client(FakeTG())
        assert tgmain.main(["x", "check"]) == 0
        tgclient.set_client(FakeTG(fail=RuntimeError("telegram getMe: 401")))
        assert tgmain.main(["x", "check"]) == 1
    finally:
        tgclient.set_client(None)
    monkeypatch.delenv("PLAN24_TG_TOKEN")
    assert tgmain.main(["x", "check"]) == 2


@needs_openssl
def test_get_client_uses_relay_settings(monkeypatch, tmp_path):
    crt, _ = make_cert(tmp_path)
    monkeypatch.setenv("PLAN24_TG_TOKEN", TOKEN)
    monkeypatch.setenv("PLAN24_TG_SECRET", "s")
    monkeypatch.setenv("PLAN24_TG_API_BASE", "https://72.56.68.43/")
    monkeypatch.delenv("PLAN24_TG_CA_FILE", raising=False)
    monkeypatch.setenv("PLAN24_TG_CA_PEM", base64.b64encode(crt.read_bytes()).decode())
    from plan24.telegram import client as tgclient
    tgclient.set_client(None)
    c = tgclient.get_client()
    assert c._base == "https://72.56.68.43"
