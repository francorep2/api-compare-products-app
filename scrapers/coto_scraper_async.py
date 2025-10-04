# scrapers_async/coto_scraper_async.py
from __future__ import annotations
from typing import List, Dict, Optional, Any
import asyncio, re, json, unicodedata
from urllib.parse import quote_plus
from dataclasses import dataclass

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200
HARD_TIMEOUT_SEC = 11.5  # para no pasarnos del límite 15s arriba

try:
    from scrapers._utils_promos import PROMO_NORMALIZER
except Exception:
    def PROMO_NORMALIZER(text: Optional[str]) -> Optional[str]:
        return text

# ---------------- Texto helpers ----------------
def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q)
    must = []
    # Heurística simple: todas las palabras de q deben aparecer
    for w in s.split():
        if w and w not in t:
            return False
    return True

# ---------------- Parsing helpers ----------------
@dataclass
class CotoItem:
    title: str
    price: Optional[str]
    url: str
    image_url: Optional[str]
    barcode: Optional[str]
    promotion: Optional[str]

def _to_search_item(raw: Dict[str, Any]) -> Optional[CotoItem]:
    title = raw.get("title") or raw.get("name") or ""
    if not title:
        return None
    url = raw.get("url") or raw.get("permalink") or ""
    if url and url.startswith("/"):
        url = COTO_BASE + url

    # Precio: puede venir como número o string con "ARS ..."
    price = raw.get("price")
    if isinstance(price, (int, float)):
        price = f"ARS {price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    elif isinstance(price, str) and not price.startswith("ARS"):
        # intenta normalizar
        num = re.findall(r"\d+[.,]?\d*", price)
        if num:
            n = num[0].replace(".", "").replace(",", ".")
            try:
                price = f"ARS {float(n):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
            except Exception:
                pass

    image = raw.get("image") or raw.get("image_url") or raw.get("thumbnail")
    if image and image.startswith("//"):
        image = "https:" + image
    if image and image.startswith("/"):
        image = COTO_STATIC_HOST + image

    barcode = raw.get("barcode") or raw.get("ean") or raw.get("gtin")
    promo = raw.get("promotion") or raw.get("promo") or raw.get("badges") or raw.get("offer")
    if isinstance(promo, (list, dict)):
        promo = json.dumps(promo, ensure_ascii=False)
    promo = PROMO_NORMALIZER(promo) if promo else None

    return CotoItem(
        title=title.strip(),
        price=price,
        url=url,
        image_url=image,
        barcode=barcode,
        promotion=promo,
    )

def _search_json_to_items(data: Any) -> List[CotoItem]:
    items: List[CotoItem] = []
    if not data:
        return items

    # Intenta paths comunes
    candidates = []
    if isinstance(data, dict):
        for key in ("results", "items", "products", "hits"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
        if not candidates:
            # a veces el JSON viene bajo data.data.xxx
            inner = data.get("data") or {}
            for key in ("results", "items", "products", "hits"):
                if key in inner and isinstance(inner[key], list):
                    candidates = inner[key]
                    break
    elif isinstance(data, list):
        candidates = data

    for obj in candidates:
        itm = _to_search_item(obj or {})
        if itm:
            items.append(itm)
            if len(items) >= TOP_N:
                break
    return items

def _extract_ldjson_items(html: str) -> List[CotoItem]:
    items: List[CotoItem] = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            blob = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(blob, dict) and blob.get("@type") in ("Product", "ItemList"):
            if blob.get("@type") == "Product":
                itm = _to_search_item(blob)
                if itm:
                    items.append(itm)
            elif blob.get("@type") == "ItemList":
                for el in blob.get("itemListElement") or []:
                    if isinstance(el, dict):
                        itm = _to_search_item(el.get("item") or el)
                        if itm:
                            items.append(itm)
        if len(items) >= TOP_N:
            break
    return items[:TOP_N]

# ---------------- Core search ----------------
async def _search_fast_json(playwright, q: str) -> List[CotoItem]:
    """
    Intenta pegarle al endpoint JSON/XHR que usa Coto para la búsqueda.
    Ajusta SEARCH_ENDPOINT si cambia.
    """
    SEARCH_ENDPOINTS = [
        # Coloca aquí el endpoint real que veas en Network (XHR/fetch) al buscar en Coto:
        # Ejemplos (PUEDEN VARIAR):
        # "/api/search?query={q}&store={sucursal}&page=1&size=24"
        # "/api/catalog/search?q={q}&branchId={sucursal}&from=0&size=24"
        f"{COTO_BASE}/api/search?query={{q}}&store={COTO_SUCURSAL_ID}&page=1&size=24",
        f"{COTO_BASE}/api/catalog/search?q={{q}}&branchId={COTO_SUCURSAL_ID}&from=0&size=24",
    ]

    async with playwright.request.new_context(
        base_url=COTO_BASE,
        extra_http_headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
        },
        timeout=8_000,  # ms por request
    ) as req:
        last_err = None
        for raw in SEARCH_ENDPOINTS:
            url = raw.format(q=quote_plus(q))
            for attempt in range(3):
                try:
                    resp = await req.get(url)
                    status = resp.status
                    if status == 200:
                        data = await resp.json()
                        items = _search_json_to_items(data)
                        if items:
                            # filtro semántico rápido
                            items = [i for i in items if _title_seems_related(i.title, q)]
                            return items[:TOP_N]
                    elif status in (429, 503):
                        await asyncio.sleep(0.35 * (attempt + 1))
                        continue
                    else:
                        last_err = f"HTTP {status} {url}"
                        break
                except Exception as e:
                    last_err = repr(e)
                    await asyncio.sleep(0.2 * (attempt + 1))
        if last_err:
            raise RuntimeError(f"search_fast_json failed: {last_err}")
    return []

async def _search_fallback_html(playwright, q: str) -> List[CotoItem]:
    """
    Fallback rápido: descarga HTML de resultados y lee JSON-LD.
    """
    paths = [
        f"/s?q={quote_plus(q)}",
        f"/busqueda?q={quote_plus(q)}",
        f"/buscar?q={quote_plus(q)}",
    ]
    async with playwright.request.new_context(
        base_url=COTO_BASE,
        extra_http_headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=7_000,
    ) as req:
        for path in paths:
            try:
                resp = await req.get(path)
                if resp.status != 200:
                    continue
                html = await resp.text()
                items = _extract_ldjson_items(html)
                if items:
                    items = [i for i in items if _title_seems_related(i.title, q)]
                    return items[:TOP_N]
            except Exception:
                continue
    return []

# ===================== Entry point =====================
async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict[str, Any]]:

    async def _run(playwright):
        query = barcode if barcode else q  # usa barcode como query si viene
        if not query:
            return []

        try:
            items = await _search_fast_json(playwright, query)
            if not items:
                items = await _search_fallback_html(playwright, query)
        except Exception:
            items = await _search_fallback_html(playwright, query)

        out = []
        for it in items[:limit]:
            out.append({
                "title": it.title,
                "price": it.price,
                "url": it.url,
                "image_url": it.image_url,
                "barcode": it.barcode,
                "promotion": it.promotion,
            })
        return out

    # Ejecuta con hard timeout
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            return await asyncio.wait_for(_run(pw), timeout=HARD_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        return []