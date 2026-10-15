# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from fastapi import FastAPI, HTTPException
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.routers.scrap import router as scrap_router
from app.db import Base, engine
from app.auth import models as _  # noqa: F401
from app.routers import user, barcode, support, list_comparator
from app.routers.search_config import router as search_config_router

from contextlib import asynccontextmanager
import asyncio

import os

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.settings import APP_ENV
from app.providers import close_http, warm_up


@asynccontextmanager
async def lifespan(app):
    # En producción el esquema lo maneja Alembic (ver Dockerfile); en dev creamos las tablas al vuelo.
    if APP_ENV == "dev":
        Base.metadata.create_all(bind=engine)
    # Conexiones HTTP/2 precalentadas a las tiendas (no bloquea el arranque)
    warm = asyncio.create_task(warm_up())
    try:
        yield
    finally:
        warm.cancel()
        await close_http()


app = FastAPI(title="Changuito API (beta)", version="2.0.0-beta", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS para la versión web. CORS_ORIGINS = lista separada por comas; CORS_ORIGIN_REGEX para previews.
_default_origins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()],
    allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX") or None,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/health/db")
def health_db():
    """Toca la base: sirve de ping periódico para que Supabase free no pause el proyecto."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Base de datos no disponible")
    return {"status": "ok", "db": "ok"}
