# app/services/coto_barcode.py
from typing import List
from urllib.parse import quote_plus, urlparse
from app.core.browser import browser_manager
from app.models.search_item import SearchItem
from app.services.helpers_vtex import parse_json_ld, normalize_item

GOOGLE = "https://www.google.com/search?q="
COTO_DOMAINS = ("cotodigital3.com.ar", "www.cotodigital3.com.ar")

async def _first_coto_result_url(page) -> str | None:
    for h3 in await page.query_selector_all("a h3"):
        link = await h3.evaluate("el => el.closest('a')?.href || ''")
        if not link:
            continue
        netloc = urlparse(link).netloc.lower()
        if any(d in netloc for d in COTO_DOMAINS):
            return link
    for a in await page.query_selector_all("a"):
        href = await a.get_attribute("href") or ""
        if href and any(d in href for d in COTO_DOMAINS):
            return href
    return None

async def search_coto_by_barcode(barcode: str, limit: int = 3) -> List[SearchItem]:
    results: List[SearchItem] = []
    q = f"site:cotodigital3.com.ar {barcode}"
    url = GOOGLE + quote_plus(q)

    async with browser_manager.new_context() as ctx:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=35000)

        try:
            btn = await page.query_selector("button:has-text('Acepto'), button:has-text('Aceptar todo'), button:has-text('Estoy de acuerdo')")
            if btn:
                await btn.click()
                await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        target = await _first_coto_result_url(page)
        if not target:
            return results

        p2 = await ctx.new_page()
        try:
            await p2.goto(target, wait_until="domcontentloaded", timeout=35000)
            results.append(await normalize_item(p2, p2.url, await parse_json_ld(p2)))
        finally:
            await p2.close()

    return results[:limit]
