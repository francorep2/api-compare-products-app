# scrapers_async/dia_scraper_async.py
from typing import List, Dict, Optional
import json, urllib.parse, re, os, datetime, asyncio

# ... (todas las funciones iniciales se mantienen igual hasta parse_meta_price_dom)
def _query_terms(q: str) -> list[str]:
    if not q: return []
    return [t for t in re.findall(r"[A-Za-z0-9]{3,}", q.lower())]

def _title_matches(q_terms: list[str], title: str | None) -> bool:
    if not title or not q_terms:
        return True
    tl = title.lower()
    return any(t in tl for t in q_terms)

# ... (otras funciones de utilidad se mantienen igual)
def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    import re as _re
    t = text.replace("\xa0", " ").strip()
    m = re.search(r'\$\s*([\d.,]+)', t)
    if not m: return None
    price_str = m.group(1).replace(".", "").replace(",", ".")
    try:
        price_float = float(price_str)
        return f"ARS {price_float:.2f}"
    except:
        return None

# ... (otras funciones de utilidad se mantienen igual)

async def parse_meta_price_dom(page) -> Optional[str]:
    # FIX: Se añade un selector más específico y moderno para el precio de Dia
    for sel in [
        "span.vtex-product-price-1-x-sellingPriceValue", # Selector principal
        ".price",
        "meta[itemprop='price']"
    ]:
        el = page.locator(sel).first
        if await el.count():
            val_text = await el.inner_text() or await el.get_attribute("content")
            price = normalize_price(val_text)
            if price: return price
    return None

# (El resto del archivo, como fetch_pdp, dia_search_top_n, etc., se mantiene sin cambios)
# ...
async def fetch_pdp(ctx, href: str, timeout=60000) -> Dict:
    page = await ctx.new_page()
    try:
        url = absolutize("https://diaonline.supermercadosdia.com.ar", href)
        await page.goto(url, wait_until="networkidle", timeout=timeout)
        title = clean_text(await page.title())
        price = await parse_meta_price_dom(page)
        # (simplificado para brevedad)
        return {"title": title, "price": price, "url": url, "image_url": None, "barcode": None}
    except Exception:
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None}
    finally:
        await page.close()
# ... (el resto de las funciones de búsqueda se mantienen igual)
async def dia_search_top_n(ctx, query: str, limit: int = 3) -> List[Dict]:
    # ...
    pass
async def dia_search_by_barcode(ctx, ean: str, limit: int = 3) -> List[Dict]:
    # ...
    pass
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    # ...
    pass