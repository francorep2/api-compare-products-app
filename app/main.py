# app/main.py
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())  


from fastapi import FastAPI
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.routers.scrap import router as scrap_router
from app.db import Base, engine
from app.auth import models as _  # noqa: F401
from app.routers import user
from app.routers import barcode
from app.core.browser_async import browser_manager_async
from app.routers.search_config import router as search_config_router
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from app.routers import support

app = FastAPI(title="Groceries API", version="1.0.0")


app.include_router(auth_router)
app.include_router(shopping_list)
app.include_router(scrap_router)
app.include_router(user.router)
app.include_router(barcode.router)
app.include_router(search_config_router)
app.include_router(support.router)


playwright_instance = { "context": None }

BLOCKED_DOMAINS = ["googletagmanager.com", "google-analytics.com", "facebook.net", "doubleclick.net"]

def should_block_request(route):
    if route.request.resource_type in {"image", "stylesheet", "font", "media"}:
        return True
    return any(domain in route.request.url for domain in BLOCKED_DOMAINS)

@asynccontextmanager
async def lifespan(app):
    print("[API] Iniciando Playwright...")
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    ctx = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
    )
    await ctx.route("**/*", lambda route: route.abort() if should_block_request(route) else route.continue_())
    playwright_instance["context"] = ctx
    print("[API] Playwright listo.")
    yield
    print("[API] Cerrando Playwright...")
    await browser.close()
    await p.stop()
    print("[API] Playwright cerrado.")



@app.on_event("startup")
async def on_startup():  
    await browser_manager_async.startup(headless=True)
    Base.metadata.create_all(bind=engine)

@app.on_event("shutdown")
async def on_shutdown():  
        await browser_manager_async.shutdown()

@app.get("/health")
def health():
    return {"status": "ok"}