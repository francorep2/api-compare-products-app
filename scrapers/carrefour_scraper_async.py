# scrapers/carrefour_scraper_async.py

import re
import time
import asyncio
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext

from app.models.search_item import SearchItem

# ---------- Constantes ----------
BASE = "https://www.carrefour.com.ar"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
BARCODE_RE = re.compile(r"^\d{8,14}$")

# ---------- Estado global ----------
_browser: Optional[Browser] = None
_ctx: Optional[BrowserContext] = None
_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None

# Caché en memoria (TTL corto para velocidad percibida)
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_CACHE_TTL = 90.0  # segundos


# ---------- Infra ----------
async def get_httpx_client() -> httpx.AsyncClient:
    """Cliente httpx global (HTTP/2 + keep-alive) para reducir latencia."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None:
        _HTTPX_CLIENT = httpx.AsyncClient(
            http2=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate, br",
            },
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )
    return _HTTPX_CLIENT


async def get_carrefour_context() -> BrowserContext:
    """BrowserContext global reusado para Playwright (solo si hace falta DOM)."""
    global _browser, _ctx
    if _ctx and not _ctx.is_closed():
        return _ctx
    if not _browser:
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(headless=True)
    _ctx = await _browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 800})
    return _ctx


# ---------- Helpers ----------
def _only_digits(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    return d or None


def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    return f"{BASE}{u}" if u.startswith("/") else u


def _clean_price(val: Optional[float | int]) -> Optional[str]:
    if val is None:
        return None
    # Formato moneda argentina: ARS 1.234,56
    price_str = f"{float(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"ARS {price_str}"


def _short_promo(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = " ".join(str(text).split()).lower()
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo:
        return f"{m_combo.group(1)}x{m_combo.group(2)}"
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    if m_pct:
        pct = m_pct.group(1)
        if any(w in t for w in ["2da", "segunda", "llevando 2"]):
            return f"2da al {pct}%"
        return f"{pct}% OFF"
    return text


def _find_barcode_in_vtex_json(p: Dict) -> Optional[str]:
    try:
        items = p.get("items", [])
        if items and items[0]:
            ean = _only_digits(items[0].get("ean"))
            if ean and 8 <= len(ean) <= 14:
                return ean
            product_id = _only_digits(p.get("productId"))
            if product_id and 12 <= len(product_id) <= 14:
                return product_id
    except (KeyError, IndexError):
        pass
    return None


def _extract_promo_from_offer(co: Dict) -> Optional[str]:
    """Recupera promo desde la oferta VTEX (tolerante a distintas claves)."""
    if not isinstance(co, dict):
        return None
    lower = {str(k).lower(): v for k, v in co.items()}

    # 1) Teasers
    teasers = lower.get("teasers") or []
    if isinstance(teasers, list) and teasers:
        name = (teasers[0].get("name") or teasers[0].get("Name") or "").strip()
        if name:
            return _short_promo(name) or name

    # 2) DiscountHighLight
    dhl = lower.get("discounthighlight") or []
    if isinstance(dhl, list) and dhl:
        name = (dhl[0].get("name") or dhl[0].get("Name") or "").strip()
        if name:
            return _short_promo(name) or name

    # 3) promotions (menos común)
    promos = lower.get("promotions") or []
    if isinstance(promos, list) and promos:
        txt = promos[0] if isinstance(promos[0], str) else (promos[0].get("name") or "")
        txt = (txt or "").strip()
        if txt:
            return _short_promo(txt) or txt

    return None


def _product_json_to_item(p: Dict) -> Dict:
    """Mapea el JSON de VTEX a nuestro esquema y extrae promos si vienen en la API."""
    title = p.get("productName")
    price, image_url, promo = None, None, None
    try:
        items = p.get("items", [])
        if items:
            image_url = items[0].get("images", [{}])[0].get("imageUrl")
            sellers = items[0].get("sellers", [])
            if sellers:
                co = sellers[0].get("commertialOffer", {}) or {}
                price = _clean_price(co.get("Price"))
                promo = _extract_promo_from_offer(co)
    except (KeyError, IndexError):
        pass

    return {
        "title": title,
        "url": _normalize_url(p.get("link")),
        "image_url": image_url,
        "price": price,
        "barcode": _find_barcode_in_vtex_json(p),
        "promotion": promo,
    }


# ---------- Cache helpers ----------
def _cache_get(key: str) -> Optional[List[Dict]]:
    t = time.time()
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if t - ts <= _CACHE_TTL:
        return val
    _CACHE.pop(key, None)
    return None


def _cache_put(key: str, val: List[Dict]) -> None:
    _CACHE[key] = (time.time(), val)


# ---------- Búsqueda por API (con fix multi-palabra) ----------
async def _api_search(q: str, by_ean: bool, limit: int) -> List[Dict]:
    """
    VTEX a veces ignora ft= cuando hay espacios. Probamos ft=, luego /search/<q>,
    y por último ft= con '+'.
    """
    client = await get_httpx_client()
    q = (q or "").strip()
    _from, _to = 0, max(0, limit + 2)

    referer = f"{BASE}/{quote(q)}?_q={quote(q)}&map=ft"
    common_headers = {"Referer": referer}

    # 1) ft=
    try:
        resp = await client.get(
            f"{BASE}/api/catalog_system/pub/products/search",
            params={"ft": q, "_from": _from, "_to": _to},
            headers=common_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    # 2) /search/<q>
    try:
        resp = await client.get(
            f"{BASE}/api/catalog_system/pub/products/search/{quote(q)}",
            params={"_from": _from, "_to": _to},
            headers=common_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    # 3) ft= con '+'
    try:
        resp = await client.get(
            f"{BASE}/api/catalog_system/pub/products/search",
            params={"ft": q.replace(" ", "+"), "_from": _from, "_to": _to},
            headers=common_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    return []


# ---------- Promos desde DOM (listado) ----------
async def _scrape_promotions_from_search_page(ctx: BrowserContext, q: str) -> Dict[str, str]:
    """Extrae URL -> promoción desde la página de resultados (rápido y con assets bloqueados)."""
    page = await ctx.new_page()
    promotions_map: Dict[str, str] = {}
    try:
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "font", "stylesheet", "media", "other"}
            else route.continue_(),
        )
        await page.route(
            re.compile(r"^(?!https?://(www\.)?carrefour\.com\.ar/).*$"),
            lambda r: r.abort(),
        )

        url = f"{BASE}/{quote(q)}?_q={quote(q)}&map=ft"
        await page.goto(url, timeout=7000, wait_until="domcontentloaded")

        try:
            await page.click("#onetrust-accept-btn-handler", timeout=1500)
        except:
            pass

        cards_data = await page.eval_on_selector_all(
            "section[class*='vtex-product-summary']",
            """(cards) => cards.map(n => {
                const a = n.querySelector('a');
                const url = a ? a.getAttribute('href') : '';
                const sel = [
                    'span.tooltipText',
                    '[data-testid="discount-badge"]',
                    '.discount-badge',
                    '[class*="promotion"], [class*="promo"]'
                ];
                let txt = null;
                for (const s of sel) {
                    const el = n.querySelector(s);
                    if (el && el.textContent) { txt = el.textContent.trim(); break; }
                }
                return { url, promotion: txt };
            })""",
        )

        for card in cards_data:
            normalized_url = _normalize_url(card.get("url"))
            text = card.get("promotion")
            if normalized_url and text:
                promotions_map[normalized_url] = _short_promo(text) or text

    except Exception as e:
        print(f"[carrefour] Micro-DOM promos falló: {e}")
    finally:
        await page.close()
    return promotions_map


# ---------- Promos desde página de producto (highlights) ----------
async def _scrape_product_highlights(ctx: BrowserContext, url: str) -> List[str]:
    """Extrae promos 'Adicional 20% OFF...' desde la página de detalle."""
    page = await ctx.new_page()
    texts: List[str] = []
    try:
        await page.route(
            "**/*",
            lambda r: r.abort()
            if r.request.resource_type in {"image", "font", "stylesheet", "media", "other"}
            else r.continue_(),
        )
        await page.route(
            re.compile(r"^(?!https?://(www\.)?carrefour\.com\.ar/).*$"),
            lambda r: r.abort(),
        )

        await page.goto(url, timeout=8000, wait_until="domcontentloaded")
        try:
            await page.click("#onetrust-accept-btn-handler", timeout=1500)
        except:
            pass

        nodes = await page.eval_on_selector_all(
            "div.valtech-carrefour-product-highlights-0-x-productHighlightWrapper span.valtech-carrefour-product-highlights-0-x-productHighlightText",
            "els => els.map(e => e.textContent.trim()).filter(Boolean)",
        )
        texts = [t for t in nodes if t]
    except Exception as e:
        print(f"[carrefour] error en highlights: {e}")
    finally:
        await page.close()
    return texts


# ---------- Función principal ----------
async def search_carrefour(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    include_promotions: bool = False,  # por defecto rápido
) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    is_barcode = bool(BARCODE_RE.fullmatch(query))

    # Cache hit
    cache_key = f"{query}|{limit}|{int(include_promotions)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # 1) Datos base por API (rápido)
    api_items = await _api_search(query, by_ean=is_barcode, limit=limit)
    if not api_items:
        _cache_put(cache_key, [])
        return []

    # 2) Si pediste promos y NO viene desde API, intenta DOM de listado
    if include_promotions and not is_barcode:
        need_dom_list = [i for i in api_items if not (i.get("promotion") or "").strip()]
        if need_dom_list:
            ctx = await get_carrefour_context()
            promo_map = await _scrape_promotions_from_search_page(ctx, query)
            if promo_map:
                for item in need_dom_list:
                    u = _normalize_url(item.get("url"))
                    if u in promo_map:
                        item["promotion"] = promo_map[u]

        # 3) Si aún falta promo, entra a la página de producto (highlights)
        need_detail = [i for i in api_items if not (i.get("promotion") or "").strip()]
        if need_detail:
            ctx = await get_carrefour_context()
            # limitar concurrencia y timeout por item
            sem = asyncio.Semaphore(2)

            async def enrich(it: Dict) -> None:
                url = _normalize_url(it.get("url"))
                if not url:
                    return
                async with sem:
                    try:
                        texts = await asyncio.wait_for(_scrape_product_highlights(ctx, url), timeout=7.0)
                        if texts:
                            it["promotion"] = _short_promo(texts[0]) or texts[0]
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        pass

            await asyncio.gather(*(enrich(it) for it in need_detail))

    # 4) Normalización final + dedupe
    final_items: List[Dict] = []
    seen_urls = set()
    for it in api_items:
        url = _normalize_url(it.get("url"))
        if url and url not in seen_urls:
            if not it.get("barcode") and is_barcode:
                it["barcode"] = query
            final_items.append(SearchItem(**it).dict())
            seen_urls.add(url)

    result = final_items[:limit]
    _cache_put(cache_key, result)
    return result
