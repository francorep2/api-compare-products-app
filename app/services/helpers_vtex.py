# app/services/helpers_vtex.py
import json
from typing import List, Optional, Dict
from urllib.parse import urlparse
from playwright.async_api import Page
from app.models.search_item import SearchItem

async def parse_json_ld(page: Page) -> Dict:
    scripts = await page.query_selector_all("script[type='application/ld+json']")
    for s in scripts:
        try:
            txt = (await s.inner_text()).strip()
            data = json.loads(txt)
            candidates = data if isinstance(data, list) else [data]
            for node in candidates:
                if isinstance(node, dict) and node.get("@type") in ("Product", ["Product"]):
                    return node
        except Exception:
            continue
    return {}

def _extract_from_jsonld(jsonld: Dict) -> Dict[str, Optional[str]]:
    if not jsonld:
        return {}
    title = jsonld.get("name")
    image = None
    img = jsonld.get("image")
    if isinstance(img, list) and img:
        image = img[0]
    elif isinstance(img, str):
        image = img
    offers = jsonld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = None
    if isinstance(offers, dict):
        price = offers.get("price")
        if isinstance(price, (int, float)):
            price = str(price)
    barcode = jsonld.get("gtin13") or jsonld.get("gtin") or jsonld.get("sku") or None
    return {"title": title, "image_url": image, "price": price, "barcode": barcode}

async def _guess_price_text(page: Page) -> Optional[str]:
    candidates = [
        "[data-testid='price']",
        "span[aria-label*='precio' i]",
        ".vtex-product-price-1-x-sellingPriceValue, .vtex-product-price-1-x-sellingPrice",
        ".shelf__price, .price",
    ]
    for sel in candidates:
        el = await page.query_selector(sel)
        if el:
            txt = (await el.inner_text()).strip()
            if txt:
                return txt
    return None

async def normalize_item(page: Page, url: str, jsonld: Dict) -> SearchItem:
    data = _extract_from_jsonld(jsonld)
    title = data.get("title") or (await page.title() or "").strip()
    price = data.get("price") or (await _guess_price_text(page))
    image_url = data.get("image_url")
    barcode = data.get("barcode")
    return SearchItem(
        title=title or None,
        price=price or None,
        url=url,
        image_url=image_url or None,
        barcode=barcode or None,
    )

def ensure_absolute(href: str, page: Page) -> str:
    if href.startswith("http"):
        return href
    parsed = urlparse(page.url)
    if href.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return href
