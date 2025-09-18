# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())  

import sys, asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.routers.scrap import router as scrap_router
from app.db import Base, engine
from app.db_init import init_tables_sync
from app.auth import models as _  # noqa: F401
from app.routers import user
from app.routers import barcode
from app.core.browser_async import browser_manager_async
from app.routers.search_config import router as search_config_router




app = FastAPI(title="Groceries Search API", version="1.0.0")


app.include_router(auth_router)
app.include_router(shopping_list)
app.include_router(scrap_router)
app.include_router(user.router)
app.include_router(barcode.router)
app.include_router(search_config_router)

@app.on_event("startup")
async def on_startup():  
    await browser_manager_async.startup(headless=True)
    init_tables_sync()
    Base.metadata.create_all(bind=engine)

@app.on_event("shutdown")
async def on_shutdown():  
        await browser_manager_async.shutdown()

@app.get("/health")
def health():
    return {"status": "ok"}