# scrapers_async/coto_scraper_async.py
from __future__ import annotations
from typing import List, Dict, Optional, Any
import asyncio, re, json, unicodedata, hashlib
from urllib.parse import quote_plus
from dataclasses import dataclass

# ===================== Config =====================
TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200
HARD_TIMEOUT_SEC = 11.5  # no pasarnos del límite de 15s aguas arriba

try:
    from scrapers._utils_promos import PROMO_NORMALIZER
except Exception:
    def PROMO_NORMALIZER(text: Optional[str]) -> Optional[str]:
        return text

# ===================== Texto / matching =====================
def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q or "")
    if not s:
        return True
    for w in s.split():
        if w and w not in t:
            return False
    return True

# ===================== Model & parsing =====================
@dataclass
class CotoItem:
    title: str
    price: Optional[str]
    url: str
    image_url: Optional[str]
    barcode: Optional[str]
    promotion: Optional[str]

def _to_search_item(raw: Dict[str, Any]) -> Optional[CotoItem]:
    title = (raw.get("title") or raw.get("name") or "").strip()
    if not title:
        return None

    # URL
    url = raw.get("url") or raw.get("permalink") or raw.get("link") or ""
    if url and url.startswith("/"):
        url = COTO_BASE + url

    # Precio
    price = raw.get("price") or raw.get("Precio") or raw.get("sellPrice")
    if isinstance(price, (int, float)):
        price = f"ARS {price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    elif isinstance(price, str):
        if not price.startswith("ARS"):
            nums = re.findall(r"\d+[.,]?\d*", price)
            if nums:
                n = nums[0].replace(".", "").replace(",", ".")
                try:
                    price = f"ARS {float(n):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
                except Exception:
                    pass
    else:
        # buscar nested
        p = raw.get("items") or raw.get("variants") or []
        if isinstance(p, list) and p:
            p0 = p[0]
            v = p0.get("price") if isinstance(p0, dict) else None
            if isinstance(v, (int, float)):
                price = f"ARS {v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

    # Imagen
    image = raw.get("image") or raw.get("image_url") or raw.get("thumbnail") or raw.get("images")
    if isinstance(image, list) and image:
        image = image[0]
    if image and isinstance(image, str):
        if image.startswith("//"): image = "https:" + image
        if image.startswith("/"): image = COTO_STATIC_HOST + image

    # Barcode
    barcode = raw.get("barcode") or raw.get("ean") or raw.get("gtin") or raw.get("sku")
    # (Opcional) Si no hay, generar uno pseudo-determinístico para no romper comparaciones
    if not barcode and title:
        barcode = hashlib.md5(title.encode("utf-8")).hexdigest()[:13]

    # Promos
    promo = raw.get("promotion") or raw.get("promo") or raw.get("badges") or raw.get("offer")
    if isinstance(promo, (list, dict)):
        try:
            promo = json.dumps(promo, ensure_ascii=False)
        except Exception:
            promo = str(promo)
    promo = PROMO_NORMALIZER(promo) if promo else None

    return CotoItem(
        title=title,
        price=price if isinstance(price, str) else None,
        url=url,
        image_url=image if isinstance(image, str) else None,
        barcode=str(barcode) if barcode else None,
        promotion=promo,
    )

def _search_json_to_items(data: Any) -> List[CotoItem]:
    items: List[CotoItem] = []
    if not data:
        return items

    # Soportar dicts variados o listas (VTEX suele devolver lista)
    candidates = []
    if isinstance(data, dict):
        for key in ("results", "items", "products", "hits", "data"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
        if not candidates and "data" in data and isinstance(data["data"], dict):
            for key in ("results", "items", "products", "hits"):
                if key in data["data"] and isinstance(data["data"][key], list):
                    candidates = data["data"][key]
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

# ===================== HTML fallbacks =====================
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

def _extract_next_data_items(html: str) -> List[CotoItem]:
    # Next.js: __NEXT_DATA__ con productos debajo de props/pageProps
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S|re.I)
    if not m:
        return []
    try:
        blob = json.loads(m.group(1))
    except Exception:
        return []

    def walk(o):
        results = []
        if isinstance(o, list):
            candidates = [x for x in o if isinstance(x, dict) and ("name" in x or "title" in x)]
            for c in candidates:
                itm = _to_search_item(c)
                if itm:
                    results.append(itm)
        elif isinstance(o, dict):
            for v in o.values():
                results.extend(walk(v))
        return results

    items = walk(blob)
    # Dedup por título
    seen = set()
    unique: List[CotoItem] = []
    for it in items:
        if it.title and it.title not in seen:
            seen.add(it.title)
            unique.append(it)
            if len(unique) >= TOP_N:
                break
    return unique

# ===================== Request Context (con cookies de sucursal) =====================
async def _prepare_request_ctx(playwright, accept: str):
    req = await playwright.request.new_context(
        base_url=COTO_BASE,
        extra_http_headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Accept": accept,
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        },
        timeout=8_000,
    )
    try:
        # Algunas instalaciones requieren una cookie de sucursal para devolver resultados
        await req.add_cookies([
            {"name": "SucursalId", "value": str(COTO_SUCURSAL_ID), "domain": "www.cotodigital.com.ar", "path": "/"},
            {"name": "sucursalId", "value": str(COTO_SUCURSAL_ID), "domain": "www.cotodigital.com.ar", "path": "/"},
        ])
    except Exception:
        pass
    return req

# ===================== Core search (JSON + VTEX) =====================
async def _search_fast_json(playwright, q: str) -> List[CotoItem]:
    SEARCH_ENDPOINTS = [
        # Endpoints típicos (ajustá con el XHR real si ves 404 en logs)
        f"{COTO_BASE}/api/search?query={{q}}&store={COTO_SUCURSAL_ID}&page=1&size=24",
        f"{COTO_BASE}/api/catalog/search?q={{q}}&branchId={COTO_SUCURSAL_ID}&from=0&size=24",
        # VTEX público (muy común)
        f"{COTO_BASE}/api/catalog_system/pub/products/search/?ft={{q}}&_from=0&_to=24",
    ]

    req = await _prepare_request_ctx(playwright, "application/json, text/plain, */*")
    try:
        last_err = None
        for raw in SEARCH_ENDPOINTS:
            url = raw.format(q=quote_plus(q))
            for attempt in range(3):
                try:
                    resp = await req.get(url)
                    status = resp.status
                    if status == 200:
                        text = await resp.text()
                        data = json.loads(text)
                        items = _search_json_to_items(data)
                        if items:
                            items = [i for i in items if _title_seems_related(i.title, q)]
                            return items[:TOP_N]
                    elif status in (429, 503):
                        await asyncio.sleep(0.35 * (attempt + 1))
                        continue
                    else:
                        last_err = f"HTTP {status} {url}"
                        break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(0.2 * (attempt + 1))
        if last_err:
            print("[coto]_search_fast_json empty ->", last_err)
        return []
    finally:
        await req.dispose()

# ===================== Fallback HTML (ld+json / __NEXT_DATA__) =====================
async def _search_fallback_html(playwright, q: str) -> List[CotoItem]:
    paths = [
        f"/s?q={quote_plus(q)}",
        f"/busqueda?q={quote_plus(q)}",
        f"/buscar?q={quote_plus(q)}",
    ]
    req = await _prepare_request_ctx(playwright, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    try:
        for path in paths:
            try:
                resp = await req.get(path)
                if resp.status != 200:
                    continue
                html = await resp.text()

                items = _extract_ldjson_items(html)
                if not items:
                    items = _extract_next_data_items(html)

                if items:
                    # filtro semántico suave (si q viene)
                    items = [i for i in items if _title_seems_related(i.title, q)]
                    return items[:TOP_N]
            except Exception:
                continue
        print("[coto]_search_fallback_html empty")
        return []
    finally:
        await req.dispose()

# ===================== Entry point =====================
async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict[str, Any]]:
    """
    Retorna: List[{ title, price "ARS ...", url, image_url, barcode, promotion|null }]
    Admite búsqueda por texto (q) o por código de barras (barcode).
    Hard cutoff 11.5s para no superar timeouts aguas arriba.
    """
    async def _run(playwright):
        query = barcode if barcode else q
        if not query:
            return []

        try:
            items = await _search_fast_json(playwright, query)
            if not items:
                items = await _search_fallback_html(playwright, query)
        except Exception:
            items = await _search_fallback_html(playwright, query)

        out: List[Dict[str, Any]] = []
        for it in items[: limit or TOP_N]:
            out.append({
                "title": it.title,
                "price": it.price,
                "url": it.url,
                "image_url": it.image_url,
                "barcode": it.barcode,
                "promotion": it.promotion,
            })
        return out

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            return await asyncio.wait_for(_run(pw), timeout=HARD_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        # No permitimos que este proveedor extienda el tiempo total del agregador
        return []
