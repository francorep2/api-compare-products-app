# scrapers_async/ctx.py
from types import SimpleNamespace
import httpx
from typing import Optional

class ScrapeCtx(SimpleNamespace):
    http: httpx.AsyncClient

async def build_scrape_ctx(
    *,
    user_agent: Optional[str] = None,
    timeout: float = 20.0,
) -> ScrapeCtx:
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    http = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers or None,
    )

    from playwright.async_api import async_playwright
    
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=user_agent)
    page = await context.new_page()
    return ScrapeCtx(http=http, pw=pw, browser=browser, context=context, page=page)


async def close_scrape_ctx(ctx: ScrapeCtx):
    # Cerrar httpx
    try:
        await ctx.http.aclose()
    except Exception:
        pass


    try:
        await ctx.context.close()
        await ctx.browser.close()
        await ctx.pw.stop()
    except Exception:
        pass
    return