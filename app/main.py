# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from fastapi import FastAPI
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.routers.scrap import router as scrap_router
from app.db import Base, engine
from app.auth import models as _  # noqa: F401
from app.routers import user, barcode, support, list_comparator
from app.routers.search_config import router as search_config_router

from contextlib import asynccontextmanager
from app.core.browser_async import browser_manager_async  # <- único manager

# (Opcional) si querés centralizar dominios bloqueados para pasarlos al contexto en cada provider
BLOCKED_DOMAINS = {"googletagmanager.com", "google-analytics.com", "facebook.net", "doubleclick.net"}

def should_block_request(route):
    if route.request.resource_type in {"image", "stylesheet", "font", "media"}:
        return True
    return any(d in route.request.url for d in BLOCKED_DOMAINS)

@asynccontextmanager
async def lifespan(app):
    # Arranca 1 solo browser compartido
    print("[API] Iniciando browser_manager_async...")
    await browser_manager_async.startup()  # <- sin headless; el manager decide
    print("[API] Browser listo.")

    # Si querés crear tablas al boot:
    Base.metadata.create_all(bind=engine)

    try:
        yield
    finally:
        print("[API] Cerrando browser_manager_async...")
        await browser_manager_async.shutdown()
        print("[API] Browser cerrado.")

app = FastAPI(title="Groceries API", version="1.0.0", lifespan=lifespan)

# Routers
app.include_router(auth_router)
app.include_router(shopping_list)
app.include_router(scrap_router)
app.include_router(user.router)
app.include_router(barcode.router)
app.include_router(search_config_router)
app.include_router(support.router)
app.include_router(list_comparator.router)

@app.get("/health")
def health():
    return {"status": "ok"}
