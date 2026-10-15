import importlib

import pytest


def _reload(monkeypatch, **env):
    for k in ("APP_ENV", "JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.core.settings as s

    return importlib.reload(s)


def test_production_requires_real_secret(monkeypatch):
    with pytest.raises(RuntimeError):
        _reload(monkeypatch)  # sin JWT_SECRET
    with pytest.raises(RuntimeError):
        _reload(monkeypatch, JWT_SECRET="change-me")
    ok = _reload(monkeypatch, JWT_SECRET="un-secreto-largo-y-aleatorio")
    assert ok.JWT_SECRET == "un-secreto-largo-y-aleatorio"


def test_dev_allows_fallback(monkeypatch):
    s = _reload(monkeypatch, APP_ENV="dev")
    assert s.JWT_SECRET
    # restaura el estado que usa el resto de los tests
    _reload(monkeypatch, APP_ENV="dev", JWT_SECRET="test-secret-no-usar-en-prod")
