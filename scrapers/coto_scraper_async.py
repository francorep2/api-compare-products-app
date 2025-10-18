# scrapers/coto_requests.py
from __future__ import annotations
from typing import List, Dict, Optional
import re, json, html, unicodedata, time
import urllib.parse as _url
import requests

try:
    # Si tenés tu normalizador propio, se usa. Si no, se ignora sin romper.
    from scrapers._utils_promos import PROMO_NORMALIZER as _PROMO_NORMALIZER
except Exception:
    _PROMO_NORMALIZER = lambda s: s  # fallback

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

_DEFAULT_TIMEOUT = 10
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)

# ===================== Texto / matching =====================

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    """Filtro simple de relevancia para no devolver cosas colgadas."""
    t = _norm_text(title)
    s = _norm_text(q)
    must = [w for w in s.split() if len(w) >= 3][:3]  # hasta 3 términos clave
    return all(w in t for w in must) if must else True

# ===================== Helpers de red / parse =====================

_JSON_RE = re.compile(
    r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)

_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

def _get(url: str, *, params: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    r = _SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.text

def _extract_preloaded_state(html_text: str) -> Optional[dict]:
    """Busca window.__PRELOADED_STATE__ = {...} en la página."""
    m = _JSON_RE.search(html_text)
    if not m:
        return None
    raw = m.group(1)
    # A veces viene con entities
    raw = html.unescape(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: intenta limpiar comas colgantes si existieran
        raw2 = re.sub(r",\s*}", "}", raw)
        raw2 = re.sub(r",\s*]", "]", raw2)
        try:
            return json.loads(raw2)
        except Exception:
            return None

def _extract_ldjson_products(html_text: str) -> List[dict]:
    """Extrae objetos Product desde JSON-LD (fallback si no hay PRELOADED_STATE)."""
    out = []
    for block in _LDJSON_RE.findall(html_text):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue
        # Puede venir como dict o lista
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") in ("Product", "ProductGroup"):
                out.append(obj)
    return out

def _price_to_ars_string(p: Optional[float | int | str]) -> Optional[str]:
    if p is None:
        return None
    try:
        val = float(str(p).replace(",", "."))
        return f"ARS {val:.2f}"
    except Exception:
        return None

def _abs_url(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("/"):
        return COTO_BASE + path
    return COTO_BASE.rstrip("/") + "/" + path

def _img_url_from_rel(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    # Coto usualmente sirve imágenes desde static.cotodigital3.com.ar
    if path.startswith("/"):
        return COTO_STATIC_HOST + path
    return COTO_STATIC_HOST.rstrip("/") + "/" + path

# ===================== Mapping de resultados =====================

def _map_item_from_preloaded(product: dict) -> Optional[dict]:
    """
    Intenta mapear un item desde la estructura interna de Coto (PRELOADED_STATE),
    contemplando variaciones de nombres de campos.
    """
    title = product.get("displayName") or product.get("name") or product.get("title")
    if not title:
        return None

    # URL del producto
    url = (
        product.get("url") or
        product.get("pdpUrl") or
        product.get("link") or
        product.get("productUrl")
    )
    url = _abs_url(url)

    # Imagen
    img = (
        product.get("imageUrl") or
        product.get("image") or
        product.get("mainImage") or
        product.get("thumbnail")
    )
    image_url = _img_url_from_rel(img)

    # Precio (varía mucho, contemplamos varias rutas)
    price = (
        product.get("price") or
        product.get("priceDecimal") or
        (product.get("prices") or {}).get("price") or
        (product.get("prices") or {}).get("listPrice")
    )
    price_str = _price_to_ars_string(price) or "ARS 0.00"

    # Barcode
    barcode = (
        product.get("ean") or
        product.get("barcode") or
        product.get("gtin") or
        product.get("gtin13")
    )

    # Promoción / badges
    promo_raw = (
        product.get("promotion") or
        product.get("badge") or
        product.get("promo") or
        (product.get("badges") or [{}])[0:1]
    )
    if isinstance(promo_raw, list):
        promo_raw = " ".join(str(x) for x in promo_raw if x)
    promotion = _PROMO_NORMALIZER(str(promo_raw)) if promo_raw else None

    return {
        "title": title.strip(),
        "price": price_str,
        "url": url,
        "image_url": image_url,
        "barcode": str(barcode) if barcode else None,
        "promotion": promotion or None,
    }

def _map_item_from_ldjson(obj: dict) -> Optional[dict]:
    title = obj.get("name") or obj.get("title")
    if not title:
        return None

    # Precio en "offers" (schema.org)
    price = None
    offers = obj.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
    elif isinstance(offers, list) and offers:
        price = offers[0].get("price")

    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None

    url = obj.get("url")
    return {
        "title": title.strip(),
        "price": _price_to_ars_string(price) or "ARS 0.00",
        "url": _abs_url(url or ""),
        "image_url": _img_url_from_rel(image or ""),
        "barcode": obj.get("gtin13") or obj.get("gtin") or None,
        "promotion": None,
    }

# ===================== Scraping de búsqueda =====================

def _build_search_url(q: str) -> str:
    # Coto usa páginas de búsqueda bajo /sitios/cdigi/busqueda con Ntt y idSucursal
    params = {
        "Ntt": q,
        "idSucursal": str(COTO_SUCURSAL_ID),
        "Dy": "1",  # permite resultados
    }
    return f"{COTO_BASE}/sitios/cdigi/busqueda?{_url.urlencode(params)}"

def _build_pdp_url_from_barcode(barcode: str) -> Optional[str]:
    """
    Heurística: muchas PDP aceptan ?Dy=1&idSucursal=200 y el patrón /productos/.../_/R-<code>-<code>-<sucursal>
    Sin API pública estable, primero probamos por búsqueda, tomamos el top exacto.
    """
    return None  # se resuelve por búsqueda para robustez

def _parse_search_page(html_text: str, q: str) -> List[dict]:
    items: List[dict] = []

    state = _extract_preloaded_state(html_text)
    if state:
        # Intentamos rutas típicas donde vienen los productos
        candidates = []
        # Ejemplos posibles: state["search"]["results"]["products"], state["results"], etc.
        for path in [
            ["search", "results", "products"],
            ["results", "products"],
            ["search", "products"],
            ["products"],
        ]:
            cur = state
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, list):
                candidates = cur
                break

        for p in candidates:
            mapped = _map_item_from_preloaded(p)
            if mapped and _title_seems_related(mapped["title"], q):
                items.append(mapped)

    # Fallback a JSON-LD si no hay PRELOADED_STATE o devolvió vacío
    if not items:
        for obj in _extract_ldjson_products(html_text):
            mapped = _map_item_from_ldjson(obj)
            if mapped and _title_seems_related(mapped["title"], q):
                items.append(mapped)

    # De-dup por URL
    dedup: Dict[str, dict] = {}
    for it in items:
        if it["url"] and it["url"] not in dedup:
            dedup[it["url"]] = it

    return list(dedup.values())

# ===================== API pública =====================

def search_coto_requests(q: str, limit: int = TOP_N) -> List[Dict]:
    """
    Búsqueda por texto. Devuelve hasta 'limit' items con:
      { title, price: "ARS ...", url, image_url, barcode, promotion|null }
    """
    url = _build_search_url(q)
    html_text = _get(url)
    items = _parse_search_page(html_text, q)

    # Orden básico: exactitud por substring + precio disponible
    qn = _norm_text(q)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (qn in t)
        has_price = it.get("price") not in (None, "ARS 0.00")
        return (exact, has_price, len(t))  # heurística simple

    items.sort(key=_score, reverse=True)
    return items[:limit]

def search_coto_by_barcode(barcode: str, limit: int = TOP_N) -> List[Dict]:
    """
    Búsqueda por código de barras: usamos la misma página de búsqueda pero con el EAN.
    """
    items = search_coto_requests(barcode, limit=limit)
    # Preferimos coincidencias que incluyan el EAN en título/URL si existe
    bn = str(barcode)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (bn in (it.get("barcode") or "")) or (bn in it.get("url", ""))
        return (exact, bn in t, len(t))
    items.sort(key=_score, reverse=True)
    return items[:limit]
