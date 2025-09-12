# app/services/barcode/coto_barcode.py
from typing import List, Dict, Optional
from urllib.parse import quote_plus
import aiohttp

from app.core.browser import browser_manager
from app.services.vtex_ean import vtex_search_ean
from app.services.peer_name_resolver import resolve_product_name_from_peers

# Probar ambos dominios (a veces alternan)
COTO_BASES = [
    "https://www.cotodigital3.com.ar",
    "https://www.cotodigital.com.ar",
]

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "es-AR,es;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
}

# -----------------------------
# 1) API VTEX por TEXTO (rápido)
# -----------------------------
async def _coto_api_search_by_text(q: str, limit: int = 3) -> List[Dict]:
    """
    Intenta la API pública de VTEX por texto:
      /api/catalog_system/pub/products/search/{q}?map=ft
    """
    out: List[Dict] = []
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as sess:
        for base in COTO_BASES:
            url = f"{base}/api/catalog_system/pub/products/search/{quote_plus(q)}?map=ft"
            try:
                async with sess.get(url) as r:
                    # Debug opcional:
                    # print(f"[COTO API q] {r.status} {url}")
                    if r.status != 200:
                        continue
                    data = await r.json(content_type=None)
                    if not isinstance(data, list) or not data:
                        continue
                    for prod in data[:limit]:
                        title = (
                            prod.get("productNameWithBrand")
                            or prod.get("productTitle")
                            or prod.get("productName")
                        )
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
                        out.append({
                            "title": title,
                            "price": None,      # si querés precio, abrir PDP (como en vtex_ean)
                            "url": link,
                            "image_url": image,
                            "barcode": None,    # es búsqueda textual
                        })
                    if out:
                        break
            except Exception:
                continue
    return out[:limit]

# ---------------------------------------
# 2) FT con Playwright (fallback visual)
# ---------------------------------------
async def _coto_ft_search_by_text(q: str, limit: int = 3) -> List[Dict]:
    """
    Usa FT: /{q}?_q={q}&map=ft y parsea cards visibles.
    """
    out: List[Dict] = []
    async with browser_manager.new_context(
        locale="es-AR",
        viewport={"width": 1366, "height": 800},
        user_agent=HEADERS["user-agent"],
    ) as ctx:
        page = await ctx.new_page()
        try:
            for base in COTO_BASES:
                url = f"{base}/{quote_plus(q)}?_q={quote_plus(q)}&map=ft"
                try:
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    # scroll leve por lazyload
                    try:
                        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass

                    try:
                        cards = await page.query_selector_all(
                            "[data-testid='product-card'], .vtex-product-summary-2-x-container, .product-card"
                        )
                    except Exception:
                        cards = []

                    for card in cards[:limit]:
                        try:
                            href = await card.eval_on_selector("a", "a => a?.href")
                        except Exception:
                            href = None
                        try:
                            title = await card.eval_on_selector("a, h3, h2", "el => el?.innerText?.trim()")
                        except Exception:
                            title = None
                        try:
                            img = await card.eval_on_selector("img", "i => i?.src")
                        except Exception:
                            img = None

                        if href and title:
                            out.append({
                                "title": title,
                                "price": None,
                                "url": href,
                                "image_url": img,
                                "barcode": None,
                            })
                    if out:
                        break
                except Exception:
                    continue
        finally:
            try:
                await page.close()
            except Exception:
                pass
    return out[:limit]

# ---------------------------------------
# 3) API pública por EAN + Fallback por nombre
# ---------------------------------------
async def search_coto_barcode(barcode: str, limit: int = 3) -> List[Dict]:
    """
    Flujo:
      a) Intentar por EAN con vtex_search_ean (API VTEX EAN + FT+PDP).
      b) Si no hay hits, resolver nombre desde Jumbo/Carrefour/DIA.
      c) Buscar en COTO por texto:
         - Primero API VTEX por texto
         - Si vacío, FT con Playwright
    """
    # a) EAN directo
    for base in COTO_BASES:
        items = await vtex_search_ean(base, barcode, limit)
        if items:
            return items

    # b) Resolver nombre desde tiendas pares
    q: Optional[str] = await resolve_product_name_from_peers(barcode)
    # Debug opcional: print(f"[COTO fallback q] {q}")
    if not q:
        return []

    # c.1) API por texto
    items = await _coto_api_search_by_text(q, limit)
    if items:
        return items

    # c.2) FT por texto
    return await _coto_ft_search_by_text(q, limit)
