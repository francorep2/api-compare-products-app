# scrapers/coto_requests.py
from __future__ import annotations
from typing import List, Dict, Optional
import re, json, html, unicodedata, time, logging
import urllib.parse as _url
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("scraper.coto")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

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
        "Connection": "keep-alive",
    }
)
# Retries básicos
retry = Retry(total=2, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504))
_SESSION.mount("https://", HTTPAdapter(max_retries=retry))
_SESSION.mount("http://", HTTPAdapter(max_retries=retry))

try:
    from scrapers._utils_promos import PROMO_NORMALIZER as _PROMO_NORMALIZER
except Exception:
    _PROMO_NORMALIZER = lambda s: s

# ===================== Texto / matching =====================

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q)
    must = [w for w in s.split() if len(w) >= 3][:3]
    return all(w in t for w in must) if must else True

# ===================== Red / cookies =====================

def _bootstrap_cookies():
    """
    Coto necesita sucursal seteada para devolver resultados consistentes.
    Pegamos a home con idSucursal para que el servidor nos deje cookies.
    """
    try:
        url = f"{COTO_BASE}/sitios/cdigi/home"
        _SESSION.get(url, params={"idSucursal": str(COTO_SUCURSAL_ID), "Dy": "1"}, timeout=_DEFAULT_TIMEOUT)
        # cookie defensiva
        _SESSION.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
        _SESSION.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
    except Exception as e:
        log.warning(f"[COTO] fallo bootstrap cookies: {e!r}")

def _get(url: str, *, params: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    r = _SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.text

# ===================== Extractores =====================

# Variantes comunes donde los sitios guardan el estado
_PRELOADED_RES = [
    re.compile(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL | re.IGNORECASE),
    re.compile(r"preloadedState\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL | re.IGNORECASE),
    re.compile(r"INITIAL_STATE\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL | re.IGNORECASE),
    re.compile(r"window\.__.*STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL | re.IGNORECASE),
]

_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

def _extract_preloaded_state(html_text: str) -> Optional[dict]:
    for rx in _PRELOADED_RES:
        m = rx.search(html_text)
        if not m:
            continue
        raw = html.unescape(m.group(1))
        # arregla comas colgantes
        raw2 = re.sub(r",\s*}", "}", raw)
        raw2 = re.sub(r",\s*]", "]", raw2)
        try:
            return json.loads(raw2)
        except Exception:
            continue
    return None

def _extract_ldjson_products(html_text: str) -> List[dict]:
    out = []
    for block in _LDJSON_RE.findall(html_text):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue
        arr = data if isinstance(data, list) else [data]
        for obj in arr:
            if isinstance(obj, dict) and obj.get("@type") in ("Product", "ProductGroup"):
                out.append(obj)
    return out

# Fallback por HTML: agarra tarjetas básicas
_CARD_RE = re.compile(
    r'<a[^>]*href="(?P<href>/sitios/cdigi/productos/[^"]+)"[^>]*>(?P<inner>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TITLE_RE = re.compile(r'(?:title="([^"]+)")|(?:alt="([^"]+)")', re.IGNORECASE)
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
# Busca precios como 1.234,56 o 1234.56; prefijo opcional $ o ARS
_PRICE_RE = re.compile(r'(?:\$|ARS)?\s*([0-9]{1,3}(?:[\.\s][0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:\.[0-9]{2}))')

def _parse_cards_html(html_text: str) -> List[dict]:
    items: List[dict] = []
    for m in _CARD_RE.finditer(html_text):
        href = m.group("href")
        inner = m.group("inner") or ""
        # título
        title = None
        tmatch = _TITLE_RE.search(inner)
        if tmatch:
            title = tmatch.group(1) or tmatch.group(2)
        if not title:
            # fallback: texto entre etiquetas
            txt = re.sub(r"<[^>]+>", " ", inner)
            txt = re.sub(r"\s+", " ", txt).strip()
            title = txt[:120] or "Producto Coto"

        # imagen
        img = ""
        im = _IMG_RE.search(inner)
        if im:
            img = im.group(1)

        # precio
        price_val = None
        pm = _PRICE_RE.search(inner)
        if pm:
            price_val = pm.group(1)

        item = {
            "title": title.strip(),
            "price": f"ARS {price_val}".replace("  ", " ") if price_val else "ARS 0.00",
            "url": _abs_url(href),
            "image_url": _img_url_from_rel(img),
            "barcode": None,
            "promotion": None,
        }
        items.append(item)
    return items

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
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("/"):
        return COTO_STATIC_HOST + path
    return COTO_STATIC_HOST.rstrip("/") + "/" + path

# ===================== Mapping =====================

def _map_item_from_preloaded(product: dict) -> Optional[dict]:
    title = product.get("displayName") or product.get("name") or product.get("title")
    if not title:
        return None
    url = product.get("url") or product.get("pdpUrl") or product.get("link") or product.get("productUrl") or ""
    img = product.get("imageUrl") or product.get("image") or product.get("mainImage") or product.get("thumbnail") or ""
    price = (
        product.get("price")
        or product.get("priceDecimal")
        or (product.get("prices") or {}).get("price")
        or (product.get("prices") or {}).get("listPrice")
    )
    barcode = product.get("ean") or product.get("barcode") or product.get("gtin") or product.get("gtin13")
    promo_raw = product.get("promotion") or product.get("badge") or product.get("promo") or product.get("badges")
    if isinstance(promo_raw, list):
        promo_raw = " ".join([str(x) for x in promo_raw if x])

    return {
        "title": title.strip(),
        "price": _price_to_ars_string(price) or "ARS 0.00",
        "url": _abs_url(url),
        "image_url": _img_url_from_rel(img),
        "barcode": str(barcode) if barcode else None,
        "promotion": _PROMO_NORMALIZER(str(promo_raw)) if promo_raw else None,
    }

def _map_item_from_ldjson(obj: dict) -> Optional[dict]:
    title = obj.get("name") or obj.get("title")
    if not title:
        return None
    price = None
    offers = obj.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
    elif isinstance(offers, list) and offers:
        price = offers[0].get("price")
    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    url = obj.get("url") or ""
    return {
        "title": title.strip(),
        "price": _price_to_ars_string(price) or "ARS 0.00",
        "url": _abs_url(url),
        "image_url": _img_url_from_rel(image or ""),
        "barcode": obj.get("gtin13") or obj.get("gtin") or None,
        "promotion": None,
    }

# ===================== Search =====================

def _build_search_url(q: str) -> str:
    params = {"Ntt": q, "idSucursal": str(COTO_SUCURSAL_ID), "Dy": "1"}
    return f"{COTO_BASE}/sitios/cdigi/busqueda?{_url.urlencode(params)}"

def _parse_search_page(html_text: str, q: str) -> List[dict]:
    items: List[dict] = []

    # 1) PRELOADED STATE
    state = _extract_preloaded_state(html_text)
    if state:
        candidates = []
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

    # 2) JSON-LD fallback
    if not items:
        for obj in _extract_ldjson_products(html_text):
            mapped = _map_item_from_ldjson(obj)
            if mapped and _title_seems_related(mapped["title"], q):
                items.append(mapped)

    # 3) HTML cards fallback
    if not items:
        for it in _parse_cards_html(html_text):
            if _title_seems_related(it["title"], q):
                items.append(it)

    # de-dup por URL
    seen: Dict[str, dict] = {}
    for it in items:
        u = it.get("url") or ""
        if u and u not in seen:
            seen[u] = it
    return list(seen.values())

def search_coto_requests(q: str, limit: int = TOP_N) -> List[Dict]:
    _bootstrap_cookies()
    url = _build_search_url(q)
    html_text = _get(url)
    # quick guard contra bloqueos
    if "Access Denied" in html_text or "verificá que sos humano" in html_text.lower():
        log.warning("[COTO] posible bloqueo / captcha")
        return []
    items = _parse_search_page(html_text, q)

    qn = _norm_text(q)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (qn in t)
        has_price = it.get("price") not in (None, "ARS 0.00")
        return (exact, has_price, -len(t))
    items.sort(key=_score, reverse=True)
    return items[:limit]

def search_coto_by_barcode(barcode: str, limit: int = TOP_N) -> List[Dict]:
    # usamos la misma búsqueda por Ntt=barcode
    items = search_coto_requests(str(barcode), limit=limit)
    bn = str(barcode)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (bn == (it.get("barcode") or "")) or (bn in (it.get("url") or ""))
        return (exact, bn in t, -len(t))
    items.sort(key=_score, reverse=True)
    return items[:limit]

