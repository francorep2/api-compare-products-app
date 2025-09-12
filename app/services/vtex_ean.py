# app/services/vtex_ean.py
from typing import List, Dict
import aiohttp
from urllib.parse import urlencode
from app.core.browser import browser_manager 
import asyncio
import aiohttp


HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "es-AR,es;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
}

VTEX_FQ_KEYS = [
    "skuEan",
    "alternateIds_Ean",
    "alternateIds_RefId",   # muchas tiendas guardan EAN como RefId
    "altIds_Ean",           # variantes que he visto en catálogos
    "altIds_RefId",
]

async def vtex_api_search_by_ean(base: str, ean: str, limit: int = 3) -> List[Dict]:
    items: List[Dict] = []
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as sess:
        for key in VTEX_FQ_KEYS:
            url = f"{base}/api/catalog_system/pub/products/search/?{urlencode({'fq': f'{key}:{ean}'})}"
            try:
                async with sess.get(url) as r:
                    if r.status != 200:
                        continue
                    data = await r.json(content_type=None)
                    if not isinstance(data, list) or not data:
                        continue
                    for prod in data[:limit]:
                        title = (prod.get("productNameWithBrand")
                                 or prod.get("productTitle")
                                 or prod.get("productName"))
                        link = prod.get("link") or prod.get("linkText")
                        if link and not str(link).startswith("http"):
                            link = f"{base}/{link}/p"
                        image = None
                        try:
                            it0 = (prod.get("items") or [])[0]
                            img0 = (it0.get("images") or [])[0]
                            image = img0.get("imageUrl")
                        except Exception:
                            pass
                        items.append({
                            "title": title,
                            "price": None,   # opcional: completar luego
                            "url": link,
                            "image_url": image,
                            "barcode": ean,
                        })
                    if items:
                        break
            except Exception:
                continue
    return items[:limit]

async def vtex_ft_search_and_verify_gtin(base: str, ean: str, limit: int = 3) -> List[Dict]:
    url = f"{base}/{ean}?_q={ean}&map=ft"
    out: List[Dict] = []
    async with browser_manager.new_context(locale="es-AR", viewport={"width":1366,"height":800}) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            # Cards típicas VTEX (ajustá si tus scrapers ya tienen selectores probados)
            cards = await page.query_selector_all(
                "[data-testid='product-card'], .vtex-product-summary-2-x-container, .product-card"
            )
            hrefs = []
            for card in cards[: (limit * 4)]:  # miramos algunos más por seguridad
                href = await card.eval_on_selector("a", "a => a?.href") or None
                title = await card.eval_on_selector("a, h3, h2", "el => el?.innerText?.trim()") or None
                img = await card.eval_on_selector("img", "i => i?.src") or None
                if href and href not in hrefs:
                    hrefs.append(href)
            # Abrimos cada PDP y verificamos JSON-LD
            for href in hrefs:
                try:
                    pdp = await ctx.new_page()
                    await pdp.goto(href, timeout=30000, wait_until="domcontentloaded")
                    # Tomamos todos los <script type="application/ld+json">
                    scripts = await pdp.eval_on_selector_all(
                        "script[type='application/ld+json']",
                        "nodes => nodes.map(n => n.textContent)"
                    )
                    matched = False
                    for s in scripts or []:
                        try:
                            import json
                            data = json.loads(s)
                            # Puede ser dict o lista
                            def check(obj):
                                if not isinstance(obj, dict):
                                    return False
                                gtin = (obj.get("gtin13") or obj.get("gtin") or obj.get("gtin14") or obj.get("gtin12"))
                                if gtin and str(gtin) == str(ean):
                                    return True
                                # Offers/Product anidados
                                prod = obj.get("@type") in ("Product",) and obj or None
                                if prod:
                                    g = (prod.get("gtin13") or prod.get("gtin") or prod.get("gtin14") or prod.get("gtin12"))
                                    return str(g) == str(ean)
                                return False
                            if isinstance(data, list):
                                matched = any(check(x) for x in data)
                            else:
                                matched = check(data)
                            if matched:
                                break
                        except Exception:
                            continue
                    if matched:
                        title = await pdp.title()
                        img = await pdp.eval_on_selector("img", "i => i?.src") or None
                        out.append({
                            "title": title,
                            "price": None,
                            "url": href,
                            "image_url": img,
                            "barcode": ean,
                        })
                    await pdp.close()
                    if len(out) >= limit:
                        break
                except Exception:
                    try:
                        await pdp.close()
                    except Exception:
                        pass
        finally:
            await page.close()
    return out[:limit]

async def vtex_search_ean(base: str, ean: str, limit: int = 3) -> List[Dict]:
    # 1) API con múltiples claves
    items = await vtex_api_search_by_ean(base, ean, limit)
    if items:
        return items
    # 2) FT + verificación por JSON-LD (gtin13 == EAN)
    return await vtex_ft_search_and_verify_gtin(base, ean, limit)
