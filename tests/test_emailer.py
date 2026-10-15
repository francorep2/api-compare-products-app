import importlib
import json

import pytest


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _load(monkeypatch, **env):
    for k in ("RESEND_API_KEY", "USE_SENDGRID_API", "SENDGRID_API_KEY", "SMTP_DRY_RUN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.auth.emailer as em

    return importlib.reload(em)


def test_resend_is_used_when_key_present(monkeypatch):
    em = _load(monkeypatch, RESEND_API_KEY="re_test", MAIL_FROM="Changuito <no-reply@example.com>")
    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["ua"] = req.get_header("User-agent")
        seen["payload"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr(em.urllib.request, "urlopen", fake_urlopen)
    em.send_otp_email("user@example.com", "123456", "verify")
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["auth"] == "Bearer re_test"
    assert seen["ua"]  # Cloudflare rechaza requests sin User-Agent
    assert seen["payload"]["to"] == ["user@example.com"]
    assert "123456" in seen["payload"]["text"]
    assert seen["payload"]["from"] == "Changuito <no-reply@example.com>"


def test_resend_failure_falls_back_to_console_and_strict_raises(monkeypatch, capsys):
    em = _load(monkeypatch, RESEND_API_KEY="re_test")

    def boom(*a, **k):
        raise OSError("sin red")

    monkeypatch.setattr(em.urllib.request, "urlopen", boom)
    em.send_email("user@example.com", "Asunto", "Tu código OTP es: 999999")  # no lanza
    assert "999999" in capsys.readouterr().out
    with pytest.raises(OSError):
        em.send_email("user@example.com", "Asunto", "x", strict=True)
    _load(monkeypatch)  # restaura el módulo sin Resend
