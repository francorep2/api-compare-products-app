# scrapers_async/carrefour_scraper_async.py
from typing import List, Dict, Optional
import json, urllib.parse, re, os, datetime, asyncio
import re

# ... (todas las funciones iniciales se mantienen igual hasta parse_meta_price_dom)
def _query_terms(q: str) -> list[str]:
    if not q: return []
    return [t for t in re.findall(r"[A-Za-z0-9]{3,}", q.lower())]

def _title_matches(q_terms: list[str], title: str | None) -> bool:
    if not title or not q_terms:
        return True
    tl = title.lower()
    return any(t in tl for t in q_terms)

async def _try_close_banners(page):
    selectors = [
        "[id*=onetrust-accept]", "#onetrust-accept-btn-handler",
        "button:has-text('Aceptar')", "button:has-text('Entendido')",
        "button:has-text('Continuar')", "[data-testid*=accept]"
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=1000)
                await asyncio.sleep(0.2)
        except: pass

async def _vtex_api_search(page, base_url: str, query: str, limit: int):
    api = f"{base_url}/api/catalog_system/pub/products/search/{query}"
    try:
        resp = await page.request.get(api, timeout=30000)
        if resp.ok:
            data = await resp.json()
            items = []
            for p in data[: max(limit*3, limit)]:
                title = p.get("productName") or p.get("productTitle")
                link_text = p.get("linkText") or p.get("link") or ""
                url = f"{base_url}/{link_text}/p" if link_text else p.get("link") or ""
                price = None
                image_url = None
                barcode = None
                for it in (p.get("items") or []):
                    if not barcode:
                        for rid in (it.get("referenceId") or []):
                            val = rid.get("Value")
                            if val and 8 <= len(re.sub(r"\D","",val)) <= 14:
                                barcode = re.sub(r"\D","",val); break
                        ean = it.get("ean")
                        if not barcode and ean:
                            d = re.sub(r"\D","", str(ean))
                            if 8 <= len(d) <= 14: barcode = d
                    if not image_url:
                        imgs = it.get("images") or []
                        if imgs:
                            image_url = imgs[0].get("imageUrl")
                    for seller in (it.get("sellers") or []):
                        co = (seller.get("commertialOffer") or {})
                        pr = co.get("Price")
                        if isinstance(pr, (float, int)) and pr > 0:
                            price = f"ARS {pr:.2f}"
                            break
                    if price and image_url:
                        break
                items.append({
                    "title": title, "price": price, "url": url,
                    "image_url": image_url, "barcode": barcode
                })
            return items
    except Exception as e:
        print("[LOG] VTEX API error:", e)
    return []
TOP_N = 3

CARREFOUR_BASE = "https://www.carrefour.com.ar"

def _search_url(query: str) -> str:
    return f"{CARREFOUR_BASE}/busca?ft={urllib.parse.quote(query)}"

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()

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

def _digits(s: Optional[str]) -> str:
    import re as _re
    return _re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if 8 <= len(d) <= 14 else None

def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

async def save_debug_html(page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        html = await page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html)
        print(f"[LOG] Guardado HTML: {path}")
    except Exception as e:
        print("[LOG] No pude guardar HTML:", e)

def absolutize(base: str, href: str, current_url: Optional[str] = None) -> str:
    if not href: return current_url or base
    if href.startswith("http"): return href
    return urllib.parse.urljoin(base, href)

async def parse_jsonld_product(page):
    # (sin cambios)
    pass

async def parse_meta_price_dom(page) -> Optional[str]:
    # FIX: Se añade un selector más específico y moderno para el precio de Carrefour
    for sel in [
        "span.valtech-carrefourar-price-0-x-currencyContainer", # Selector principal
        ".lyracons-carrefourar-product-price-0-x-sellingPrice", # Otro selector común
        "meta[itemprop='price']", 
        "meta[property='product:price:amount']"
    ]:
        el = page.locator(sel).first
        if await el.count():
            val_text = await el.inner_text() or await el.get_attribute("content")
            price = normalize_price(val_text)
            if price: return price
    return None

# (El resto del archivo, como fetch_pdp, carrefour_search_top_n, etc., se mantiene sin cambios)
# ...
async def fetch_pdp(ctx, href: str, timeout=60000) -> Dict:
    page = await ctx.new_page()
    try:
        url = absolutize(CARREFOUR_BASE, href)
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
async def carrefour_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    # ...
    pass
async def carrefour_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    # ...
    pass
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    # ...
    pass