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


def test_db_url_forces_psycopg2_driver():
    # SQLAlchemy 2.1 cambió el driver por defecto de "postgresql://" a psycopg 3 (no instalado)
    from app.db import normalize_db_url

    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("postgresql+psycopg2://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("sqlite://") == "sqlite://"
    assert normalize_db_url(None) is None
