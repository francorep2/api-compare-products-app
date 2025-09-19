# scrapers/carrefour_scraper_async.py (VERSIÓN HÍBRIDA OPTIMIZADA)

import re
import json
from typing import List, Dict, Optional
from urllib.parse import quote
import httpx
from playwright.async_api import BrowserContext

from app.models.search_item import SearchItem

BASE = "https://www.carrefour.com.ar"
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
    # Formato de moneda Argentina
    price_str = f"{float(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"ARS {price_str}"

def _short_promo(text: Optional[str]) -> Optional[str]:
    # Esta es una versión simplificada, podés usar tu función más compleja si preferís
    if not text: return None
    t = " ".join(str(text).split()).lower()
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo: return f"{m_combo.group(1)}x{m_combo.group(2)}"
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    if m_pct:
        pct = m_pct.group(1)
        if any(w in t for w in ["2da", "segunda", "llevando 2"]): return f"2da al {pct}%"
        return f"{pct}% OFF"
    return text # Devolvemos el texto original si no coincide con un patrón simple

# --- PARSEADORES Y SCRAPERS ---

def _find_barcode_in_vtex_json(p: Dict) -> Optional[str]:
    try:
        items = p.get("items", [])
        if items and items[0]:
            ean = _only_digits(items[0].get("ean"))
            if ean and 8 <= len(ean) <= 14: return ean
            # Carrefour también usa 'productId' a veces como EAN
            product_id = _only_digits(p.get("productId"))
            if product_id and 12 <= len(product_id) <= 14: return product_id
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
    # La promoción se obtiene por separado desde el DOM
    return {"title": title, "url": _normalize_url(p.get("link")), "image_url": image_url, 
            "price": price, "barcode": _find_barcode_in_vtex_json(p), "promotion": None}

async def _api_search(q: str, by_ean: bool, limit: int) -> List[Dict]:
    """Usa httpx para obtener datos base rápidamente desde la API."""
    api_url = f"{BASE}/api/catalog_system/pub/products/search"
    # El endpoint de Carrefour usa ft= para texto y fq=productId para barcode
    if by_ean:
        # La API de Carrefour no busca bien por EAN, es mejor buscar por texto
        params = {"ft": q, "_to": str(limit - 1)}
    else:
        params = {"ft": q, "_to": str(limit * 2 - 1)}
        
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=5) as client:
            resp = await client.get(api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return [_product_json_to_item(p) for p in data] if isinstance(data, list) else []
    except Exception:
        return []

async def _scrape_promotions_from_search_page(ctx, q: str) -> Dict[str, str]:
    """Visita la página de búsqueda una vez para extraer un mapa de URL -> Promoción."""
    page = await ctx.new_page()
    promotions_map = {}
    try:
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image","font","stylesheet","media"} else route.continue_())
        url = f"{BASE}/{quote(q)}?_q={quote(q)}&map=ft"
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")

        try:
            await page.click('#onetrust-accept-btn-handler', timeout=5000)
        except:
            pass # Si no hay pop-up, no hay problema

        cards_data = await page.eval_on_selector_all(
            "section[class*='vtex-product-summary']", # Selector de las tarjetas de producto
            '''(cards) => cards.map(n => {
                const url = n.querySelector('a')?.getAttribute('href') || '';
                // Selector específico para la promo de Carrefour que me mostraste
                const promoEl = n.querySelector('span.tooltipText');
                const promotion = promoEl ? promoEl.textContent.trim() : null;
                return { url, promotion };
            })'''
        )
        
        for card in cards_data:
            normalized_url = _normalize_url(card.get("url"))
            promo_text = card.get("promotion")
            if normalized_url and promo_text:
                promotions_map[normalized_url] = promo_text
    except Exception as e:
        print(f"No se pudieron extraer promociones del DOM de Carrefour: {e}")
    finally:
        await page.close()
    return promotions_map

# --- FUNCIÓN PRINCIPAL ---

async def search(ctx: BrowserContext, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query: return []

    is_barcode = bool(BARCODE_RE.fullmatch(query))
    
    # 1. Obtenemos datos base de la API
    api_items = await _api_search(query, by_ean=is_barcode, limit=limit)
    if not api_items:
        return []

    # 2. Enriquecemos con las promociones extraídas del DOM
    # (Lo hacemos para búsquedas por texto, que es donde aparecen promos de listado)
    if not is_barcode:
        promo_map = await _scrape_promotions_from_search_page(ctx, query)
        if promo_map:
            for item in api_items:
                url = _normalize_url(item.get("url"))
                if url in promo_map:
                    item["promotion"] = promo_map[url]
    
    # 3. Limpiamos y devolvemos el resultado
    final_items = []
    seen_urls = set()
    for it in api_items:
        url = _normalize_url(it.get("url"))
        if url and url not in seen_urls:
            # Si el barcode vino nulo de la API, pero buscamos por barcode, lo asignamos
            if not it.get("barcode") and is_barcode:
                it["barcode"] = query
            final_items.append(SearchItem(**it).dict())
            seen_urls.add(url)

    return final_items[:limit]