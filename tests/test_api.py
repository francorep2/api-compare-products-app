import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.providers as providers
import app.task_db as task_db
from app.dependencies.auth import User, get_current_user
from app.main import app
from app.models.comparison import ComparisonTask

USER = {"id": "1"}


def _item(store, title, price):
    return {
        "title": title, "price": f"ARS {price:.2f}", "url": f"https://{store}/x",
        "image_url": None, "barcode": None, "promotion": None,
    }


class FakeStore:
    def __init__(self, name, catalog):
        self.name, self.catalog = name, catalog

    async def search(self, q, barcode, limit):
        word = (q or "").split()[0].lower() if q else ""
        return [_item(self.name, t, p) for t, p in self.catalog if word and word in t.lower()][:limit]


@pytest.fixture()
def client(monkeypatch):
    # tiendas falsas: sin red
    stores = {
        "coto": FakeStore("coto", [("Leche Entera Coto", 100), ("Arroz Gallo Coto", 300)]),
        "dia": FakeStore("dia", [("Leche Entera Dia", 90), ("Arroz Gallo Dia", 350)]),
        "jumbo": FakeStore("jumbo", []),
        "carrefour": FakeStore("carrefour", [("Leche Entera Carrefour", 95)]),
    }
    for name, store in stores.items():
        monkeypatch.setitem(providers.STORES, name, store)
    providers.http._cache.clear()
    # DB en memoria para las tareas
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ComparisonTask.__table__.create(engine)
    monkeypatch.setattr(task_db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    USER["id"] = "1"
    app.dependency_overrides[get_current_user] = lambda: User(id=USER["id"])
    yield TestClient(app)  # sin "with": no corre el lifespan
    app.dependency_overrides.clear()


def test_search_requires_query(client):
    assert client.get("/search/all").status_code == 400


def test_search_all_keeps_four_keys_and_respects_enabled(client):
    res = client.get("/search/all", params={"q": "leche", "enabled": ["coto"]}).json()["results"]
    assert set(res) == {"coto", "carrefour", "dia", "jumbo"}
    assert len(res["coto"]) == 1 and res["dia"] == []


def test_barcode_falls_back_to_name_of_other_store(client, monkeypatch):
    class EanOnlyCoto(FakeStore):
        async def search(self, q, barcode, limit):
            if barcode:
                return [_item("coto", "Leche Entera Coto", 100)]
            return await super().search(q, barcode, limit)

    monkeypatch.setitem(providers.STORES, "coto", EanOnlyCoto("coto", [("Leche Entera Coto", 100)]))
    out = client.get("/search/barcode/all", params={"barcode": "7790742348005"}).json()
    assert out["results"]["coto"] and out["results"]["dia"]  # dia no tiene el EAN: lo halló por nombre


def test_compare_flow_and_ownership(client):
    body = {"items": [{"nombre": "leche entera", "cantidad": 2}, {"nombre": "arroz gallo", "cantidad": 1}]}
    tid = client.post("/compare/start", json=body).json()["task_id"]
    res = client.get(f"/compare/results/{tid}").json()
    assert res["status"] == "completed"
    totals = res["data"]["totals_by_store"]
    assert totals["coto"]["total"] == 500 and totals["dia"]["total"] == 530
    assert totals["carrefour"]["missing_items"] == ["arroz gallo"]
    assert res["data"]["winner"] == "coto"  # jumbo/carrefour están incompletos: no pueden ganar

    USER["id"] = "2"
    assert client.get(f"/compare/results/{tid}").status_code == 404
    assert client.get("/compare/results/no-existe").status_code == 404


def test_health_db_touches_database(client):
    assert client.get("/health/db").json() == {"status": "ok", "db": "ok"}


def test_health_db_returns_503_when_database_down(client, monkeypatch):
    import app.main as main

    class BrokenEngine:
        def connect(self):
            raise RuntimeError("db caída")

    monkeypatch.setattr(main, "engine", BrokenEngine())
    r = client.get("/health/db")
    assert r.status_code == 503
    assert "db caída" not in r.text


def test_cors_preflight_allows_web_origin(client):
    r = client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.options("/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
