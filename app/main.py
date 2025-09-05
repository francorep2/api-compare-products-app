# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())  

import sys, asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool   
from app.core.browser import browser_manager
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.routers.scrap import router as scrap_router
from app.db_init import init_tables_sync


app = FastAPI(title="Groceries Search API", version="1.0.0")

@app.on_event("startup")
async def on_startup():  
    await run_in_threadpool(browser_manager.startup, True)
    init_tables_sync()

app.include_router(auth_router)
app.include_router(shopping_list)
app.include_router(scrap_router)

@app.on_event("shutdown")
async def on_shutdown():  
    await run_in_threadpool(browser_manager.shutdown)


@app.get("/health")
def health():
    return {"status": "ok"}