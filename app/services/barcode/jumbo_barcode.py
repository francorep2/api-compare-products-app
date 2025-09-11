from typing import List
from urllib.parse import urlparse
from playwright.async_api import Page
from app.core.browser_async import browser_manager_async as browser_manager
from app.models.search_item import SearchItem
import json

BASE = "https://www.jumbo.com.ar"

async def _parse_json_ld(page: Page) -> dict:
    for s in await page.query_selector_all("script[type='application/ld+json']"):
        try:
            data = json.loads((await s.inner_text()).strip())
            nodes = data if isinstance(data, list) else [data]
            for n in nodes:
                if isinstance(n, dict) and n.get("@type") in ("Product", ["Product"]):
                    return n
        except Exception:
            pass
    return {}

def _abs_url(href: str, page: Page) -> str:
    if href.startswith("http"):
        return href
    parsed = urlparse(page.url)
    return f"{parsed.scheme}://{parsed.netloc}{href}" if href.startswith("/") else href

async def _normalize(page: Page, url: str, jsonld: dict) -> SearchItem:
    name = jsonld.get("name") if jsonld else None
    image = None
    img = jsonld.get("image") if jsonld else None
    if isinstance(img, list) and img:
        image = img[0]
    elif isinstance(img, str):
        image = img
    offers = (jsonld.get("offers") or {}) if jsonld else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price")
    if isinstance(price, (int, float)):
        price = str(price)

    if not name:
        name = (await page.title() or "").strip()
    if not price:
        for sel in [
            "[data-testid='price']",
            ".vtex-product-price-1-x-sellingPriceValue, .vtex-product-price-1-x-sellingPrice",
            ".price, .shelf__price",
        ]:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    price = txt
                    break

    barcode = (jsonld.get("gtin13") or jsonld.get("gtin") or jsonld.get("sku") or None) if jsonld else None
    return SearchItem(
        title=name or None,
        price=price or None,
        url=url,
        image_url=image or None,
        barcode=barcode or None,
    )

async def search_jumbo_by_barcode(barcode: str, limit: int = 3) -> List[SearchItem]:
    out: List[SearchItem] = []
    search_url = f"{BASE}/{barcode}?_q={barcode}&map=ft"

    async with browser_manager.new_context() as ctx:
        page = await ctx.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        jsonld = await _parse_json_ld(page)
        if jsonld:
            out.append(await _normalize(page, page.url, jsonld))
            return out[:limit]

        for sel in [
            "a.vtex-product-summary-2-x-clearLink",
            "a[data-testid='product-summary-link']",
            "a[href*='/p']",
        ]:
            links = await page.query_selector_all(sel)
            if links:
                for a in links[:limit]:
                    href = (await a.get_attribute("href")) or ""
                    if not href:
                        continue
                    url = _abs_url(href, page)
                    p2 = await ctx.new_page()
                    try:
                        await p2.goto(url, wait_until="domcontentloaded", timeout=30000)
                        out.append(await _normalize(p2, p2.url, await _parse_json_ld(p2)))
                    finally:
                        await p2.close()
                break

    return out[:limit]
