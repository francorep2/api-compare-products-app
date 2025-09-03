# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())  

import sys, asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from app.core.browser import browser_manager
from app.models import SearchItem, SearchResponse
from app.services.coto import search_coto
from app.services.dia import search_dia  
from app.services.carrefour import search_carrefour
from starlette.concurrency import run_in_threadpool   
from app.services.jumbo import search_jumbo  
from app.core.db import Base, engine
from app.auth.router import router as auth_router
from app.core.db import Base, engine


app = FastAPI(title="Groceries Search API", version="1.0.0")

@app.on_event("startup")
async def on_startup():  
    await run_in_threadpool(browser_manager.startup, True)
    Base.metadata.create_all(bind=engine)

app.include_router(auth_router)


@app.on_event("shutdown")
async def on_shutdown():  
    await run_in_threadpool(browser_manager.shutdown)

@app.get("/search/coto", response_model=SearchResponse)
def search_coto_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_coto(q, limit=limit)]
    return SearchResponse(source="coto", items=items)

@app.get("/search/carrefour", response_model=SearchResponse)
def search_carrefour_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_carrefour(q, limit=limit)]
    return SearchResponse(source="carrefour", items=items)

@app.get("/search/dia", response_model=SearchResponse)
def search_dia_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_dia(q, limit=limit)]
    return SearchResponse(source="dia", items=items)

@app.get("/search/jumbo", response_model=SearchResponse)
def search_jumbo_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_jumbo(q, limit=limit)]
    return SearchResponse(source="jumbo", items=items)

@app.get("/search/all")
def search_all_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    coto_items = search_coto(q, limit=limit)
    carrefour_items = search_carrefour(q, limit=limit)
    dia_items = search_dia(q, limit=limit)
    jumbo_items = search_jumbo(q, limit=limit)   
    return {
        "coto": coto_items,
        "carrefour": carrefour_items,
        "dia": dia_items,
        "jumbo": jumbo_items
    }

