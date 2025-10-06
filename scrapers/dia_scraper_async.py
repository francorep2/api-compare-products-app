# scrapers/dia_scraper_async.py (VERSIÓN HÍBRIDA FINAL)

import re
import json
from typing import List, Dict, Optional
from urllib.parse import quote
import httpx

from app.models.search_item import SearchItem

BASE = "https://diaonline.supermercadosdia.com.ar"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BARCODE_RE = re.compile(r"^\d{8,14}$")

# --- UTILS Y HELPERS ---

def _only_digits(s: Optional[str]) -> Optional[str]:
    if not s: return None
    d = re.sub(r"\D", "", s)
    return d or None

def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u: return None
    return f"{BASE}{u}" if u.startswith("/") else u

def _clean_price(val: Optional[float | int]) -> Optional[str]:
    if val is None: return None
    return f"ARS {float(val):.2f}"
    
def _short_promo(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = " ".join(str(text).split()).lower()
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo: return f"{m_combo.group(1)}x{m_combo.group(2)}"
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    if m_pct:
        pct = m_pct.group(1)
        if any(w in t for w in ["2da", "segunda", "llevando 2"]): return f"2da al {pct}%"
        return f"{pct}% OFF"
    if "savingspercentage" in t:
        digits = _only_digits(t)
        if digits: return f"{digits}% OFF"
    return None

# --- PARSEADORES Y SCRAPERS ---

def _find_barcode_in_vtex_json(p: Dict) -> Optional[str]:
    try:
        items = p.get("items", [])
        if items and items[0]:
            ean = _only_digits(items[0].get("ean"))
            if ean and 8 <= len(ean) <= 14: return ean
            ref_ids = items[0].get("referenceId", [])
            if ref_ids and ref_ids[0]:
                ref_val = _only_digits(ref_ids[0].get("Value"))
                if ref_val and 8 <= len(ref_val) <= 14: return ref_val
    except (KeyError, IndexError): pass
    return None

def _product_json_to_item(p: Dict) -> Dict:
    title = p.get("productName")
    price, image_url = None, None
    try:
        items = p.get("items", [])
        if items:
            image_url = items[0].get("images", [{}])[0].get("imageUrl")
            sellers = items[0].get("sellers", [])
            if sellers:
                co = sellers[0].get("commertialOffer", {})
                price = _clean_price(co.get("Price"))
    except (KeyError, IndexError): pass
    return {"title": title, "url": _normalize_url(p.get("link")), "image_url": image_url, 
            "price": price, "barcode": _find_barcode_in_vtex_json(p), "promotion": None}

async def _api_search(q: str, by_ean: bool, limit: int) -> List[Dict]:
    api_url = f"{BASE}/api/catalog_system/pub/products/search"
    params = {"_to": str(limit * 2 - 1)}
    if by_ean:
        params["fq"] = f"alternateIds_Ean:{q}"
    else:
        params["ft"] = q
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=5) as client:
            resp = await client.get(api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return [_product_json_to_item(p) for p in data] if isinstance(data, list) else []
    except Exception:
        return []

async def _scrape_promotions_from_search_page(ctx, q: str) -> Dict[str, str]:
    """
    NUEVA FUNCIÓN: Visita la página de búsqueda una vez para extraer un mapa de URL -> Promoción.
    """
    page = await ctx.new_page()
    promotions_map = {}
    try:
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image","font","stylesheet","media"} else route.continue_())
        url = f"{BASE}/{quote(q)}?_q={quote(q)}&map=ft"
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        
        cards_data = await page.eval_on_selector_all(
            ".vtex-product-summary-2-x-container",
            '''(cards) => cards.map(n => {
                const url = n.querySelector('a')?.getAttribute('href') || '';
                const promo_els = n.querySelectorAll('[class*="savingsPercentage"], [class*="discountBadgeText"], [class*="productHighlightText"]');
                const promotion = Array.from(promo_els).map(el => el.textContent.trim()).join(' | ');
                return { url, promotion };
            })'''
        )
        
        for card in cards_data:
            normalized_url = _normalize_url(card.get("url"))
            short_promo = _short_promo(card.get("promotion"))
            if normalized_url and short_promo:
                promotions_map[normalized_url] = short_promo
    except Exception as e:
        print(f"No se pudieron extraer promociones del DOM de DIA: {e}")
    finally:
        await page.close()
    return promotions_map


# --- FUNCIÓN PRINCIPAL ---

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query: return []

    is_barcode = bool(BARCODE_RE.fullmatch(query))
    
    # 1. Siempre obtenemos los datos base de la API, que es la más rápida.
    api_items = await _api_search(query, by_ean=is_barcode, limit=limit)
    if not api_items:
        return []

    # 2. Si es una búsqueda por texto, enriquecemos los resultados con las promociones del DOM.
    if not is_barcode:
        promo_map = await _scrape_promotions_from_search_page(ctx, query)
        if promo_map:
            for item in api_items:
                url = _normalize_url(item.get("url"))
                if url in promo_map:
                    item["promotion"] = promo_map[url]
    
    # 3. Limpiamos y devolvemos el resultado final.
    final_items = []
    seen_urls = set()
    for it in api_items:
        url = _normalize_url(it.get("url"))
        if url and url not in seen_urls:
            final_items.append(SearchItem(**it).dict())
            seen_urls.add(url)
    
    return final_items[:limit]