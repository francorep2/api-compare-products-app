# app/services/vtex_ean.py
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlencode
import aiohttp
import json
import re

from app.core.browser import browser_manager

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "es-AR,es;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
}

# Probamos múltiples claves de VTEX porque cada tienda indexa distinto
VTEX_FQ_KEYS = [
    "skuEan",
    "alternateIds_Ean",
    "alternateIds_RefId",
    "altIds_Ean",
    "altIds_RefId",
]

# ----------------------
# Helpers genéricos
# ----------------------
async def _json_parse_safe(txt: str):
    try:
        return json.loads(txt)
    except Exception:
        return None

async def _eval_text_or_none(page, selector: str) -> Optional[str]:
    try:
        return await page.eval_on_selector(selector, "el => el?.innerText?.trim()")
    except Exception:
        return None

async def _eval_attr_or_none(page, selector: str, attr: str) -> Optional[str]:
    try:
        return await page.eval_on_selector(selector, f"el => el?.getAttribute('{attr}') || el?.{attr} || null")
    except Exception:
        return None

async def _extract_price_from_jsonld(scripts: List[str]) -> Optional[Tuple[Optional[str], Optional[float]]]:
    """
    Devuelve (currency, price) si encuentra offers.price / lowPrice en JSON-LD.
    """
    for s in scripts or []:
        data = await _json_parse_safe(s)
        if not data:
            continue

        def iter_objs(obj):
            if isinstance(obj, dict):
                yield obj
                for v in obj.values():
                    yield from iter_objs(v)
            elif isinstance(obj, list):
                for x in obj:
                    yield from iter_objs(x)

        for obj in iter_objs(data):
            if obj.get("@type") == "Product":
                offers = obj.get("offers")
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("lowPrice")
                    cur = offers.get("priceCurrency")
                    if price is not None:
                        try:
                            return (cur, float(str(price).replace(",", ".").strip()))
                        except Exception:
                            return (cur, None)
    return None

async def _extract_price_from_dom(page) -> Optional[Tuple[Optional[str], Optional[float]]]:
    """
    Fallback a selectores comunes de VTEX para extraer precio visible en el DOM.
    """
    price_selectors = [
        "[data-testid='price']",
        "[data-price]",
        ".vtex-product-price-1-x-sellingPriceValue",
        ".shelf__price",
        ".productPrice",
        ".price__best",
    ]
    currency_selectors = [
        ".vtex-product-price-1-x-currencyCode",
        ".vtex-product-price-1-x-currencyContainer",
    ]

    price_text = None
    for sel in price_selectors:
        price_text = await _eval_text_or_none(page, sel)
        if price_text:
            break

    currency_text = None
    for sel in currency_selectors:
        currency_text = await _eval_text_or_none(page, sel)
        if currency_text:
            break

    # Fallback: buscar “$ 1.234,56” en todo el body
    if not price_text:
        try:
            price_text = await page.eval_on_selector(
                "body",
                "b => (b.innerText.match(/\\$\\s*[0-9\\.,]+/)?.[0] || null)"
            )
        except Exception:
            price_text = None

    if price_text:
        cur = None
        if currency_text and len(currency_text) <= 4:
            cur = currency_text.strip()
        if "ARS" in (price_text or "").upper():
            cur = "ARS"

        # Normaliza "ARS $ 1.234,56" -> 1234.56
        num = (price_text or "")
        num = num.replace("ARS", "").replace("$", "").replace("\xa0", " ").strip()
        num = num.replace(".", "").replace(",", ".")

        try:
            return (cur, float(num))
        except Exception:
            return (cur, None)

    return None

# ----------------------
# API VTEX por EAN
# ----------------------
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

                        items.append({
                            "title": title,
                            "price": None,   # Se completa si luego abrimos PDP (opcional)
                            "url": link,
                            "image_url": image,
                            "barcode": ean,
                            "promotion": promo_text,
                        })
                    if items:
                        break
            except Exception:
                continue
    return items[:limit]

# ----------------------
# Enriquecimiento desde PDP (validación EAN + precio)
# ----------------------
async def _enrich_from_pdp(ctx, href: str, ean: str) -> Optional[Dict]:
    pdp = await ctx.new_page()
    try:
        await pdp.goto(href, timeout=30000, wait_until="domcontentloaded")

        # 1) JSON-LD
        try:
            scripts = await pdp.eval_on_selector_all(
                "script[type='application/ld+json']",
                "nodes => nodes.map(n => n.textContent)"
            )
        except Exception:
            scripts = []

        # 2) Validación EAN por JSON-LD (gtin13/gtin/gtin14/gtin12)
        matched = False
        for s in scripts or []:
            data = await _json_parse_safe(s)
            if not data:
                continue

            def has_gtin(obj) -> bool:
                if not isinstance(obj, dict):
                    return False
                g = obj.get("gtin13") or obj.get("gtin") or obj.get("gtin14") or obj.get("gtin12")
                return str(g) == str(ean)

            if isinstance(data, list):
                matched = any(has_gtin(x) for x in data)
            else:
                matched = has_gtin(data)

            if matched:
                break

        # 2b) Fallback (Coto y otros): buscar EAN literal en el HTML si no hay JSON-LD útil
        if not matched:
            try:
                html = await pdp.content()
            except Exception:
                html = ""
            if html and re.search(rf"\b{re.escape(str(ean))}\b", html):
                matched = True

        if not matched:
            return None  # no es el PDP correcto

        # 3) Título e imagen
        try:
            title = await pdp.title()
        except Exception:
            title = None

        img = await _eval_attr_or_none(pdp, "img", "src")

        # 4) Precio (JSON-LD primero; si no, DOM)
        currency: Optional[str] = None
        price_val: Optional[float] = None

        pj = await _extract_price_from_jsonld(scripts)
        if pj:
            currency, price_val = pj

        if price_val is None:
            pd = await _extract_price_from_dom(pdp)
            if pd:
                currency, price_val = pd

        price_str = None
        if isinstance(price_val, (int, float)):
            price_str = f"{(currency or 'ARS')} {price_val:.2f}"

        return {
            "title": title,
            "price": price_str,
            "url": href,
            "image_url": img,
            "barcode": ean,
        }
    finally:
        try:
            await pdp.close()
        except Exception:
            pass

# ----------------------
# FT + verificación PDP
# ----------------------
async def vtex_ft_search_and_verify_gtin(base: str, ean: str, limit: int = 3) -> List[Dict]:
    """
    Busca por FT (/{EAN}?_q={EAN}&map=ft), navega a los PDPs candidatos,
    valida que el EAN coincida (JSON-LD o HTML) y extrae precio si es posible.
    """
    url = f"{base}/{ean}?_q={ean}&map=ft"
    out: List[Dict] = []
    async with browser_manager.new_context(locale="es-AR", viewport={"width": 1366, "height": 800}) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")

            # Cards típicas VTEX (ajustá/añadí si tu scraper ya tiene otros selectores confiables)
            try:
                cards = await page.query_selector_all(
                    "[data-testid='product-card'], .vtex-product-summary-2-x-container, .product-card"
                )
            except Exception:
                cards = []

            hrefs: List[str] = []
            for card in cards[:12]:  # miramos hasta 12 cards
                href = None
                try:
                    href = await card.eval_on_selector("a", "a => a?.href")
                except Exception:
                    href = None
                if href and href not in hrefs:
                    hrefs.append(href)

            # Abrimos cada PDP candidato y validamos/enriquecemos
            for href in hrefs:
                enriched = await _enrich_from_pdp(ctx, href, ean)
                if enriched:
                    out.append(enriched)
                    if len(out) >= limit:
                        break
        finally:
            try:
                await page.close()
            except Exception:
                pass
    return out[:limit]

# ----------------------
# Búsqueda por EAN (público)
# ----------------------
async def vtex_search_ean(base: str, ean: str, limit: int = 3) -> List[Dict]:
    """
    1) Intenta API VTEX con múltiples claves fq=
    2) Si no hay hits, cae a FT y verifica PDP, extrayendo precio si es posible.
    """
    items = await vtex_api_search_by_ean(base, ean, limit)
    if items:
        return items
    return await vtex_ft_search_and_verify_gtin(base, ean, limit)
