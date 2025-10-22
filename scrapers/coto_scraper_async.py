from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import re, json, html, unicodedata, time, logging
import urllib.parse as _url

# 👇 cambio clave: fingerprint de Chrome (HTTP/2, JA3 real)
from curl_cffi import requests as crequests
from curl_cffi.requests import Session

log = logging.getLogger("scraper.coto")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

# Fast mode knobs
FAST_MODE_DEFAULT = True          # evita bootstrap salvo que falle
TEXT_ONLY_DEFAULT = True          # no resolvemos ni buscamos imágenes
ENRICH_BARCODE_DEFAULT = False    # no abrimos PDP salvo pedido explícito

_DEFAULT_TIMEOUT = 9  # bajamos timeout por request; total debe quedar < 12s

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Fingerprint Chrome + HTTP/2
_SESSION: Session = crequests.Session(
    impersonate="chrome",  # 🎯 la clave para pasar WAF sin browser
    timeout=_DEFAULT_TIMEOUT,
)
_SESSION.headers.update(
    {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
)

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
    """Setea sucursal en cookie. Se evita en FAST_MODE para ahorrar 1 request."""
    try:
        home = f"{COTO_BASE}/sitios/cdigi/home"
        _SESSION.get(home, params={"idSucursal": str(COTO_SUCURSAL_ID), "Dy": "1"})
        # asegura cookie en ambos dominios
        _SESSION.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
        _SESSION.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
    except Exception as e:
        log.warning(f"[COTO] bootstrap cookies falló: {e!r}")

def _get(url: str, *, params: Optional[dict] = None, timeout: Optional[float] = None) -> Tuple[str, int]:
    r = _SESSION.get(url, params=params, timeout=timeout or _DEFAULT_TIMEOUT)
    return r.text, r.status_code

# ===================== Extractores =====================

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

_CARD_RE = re.compile(
    r'<a[^>]*href="(?P<href>/sitios/cdigi/productos/[^"]+)"[^>]*>(?P<inner>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TITLE_RE = re.compile(r'(?:title="([^"]+)")|(?:alt="([^"]+)")', re.IGNORECASE)
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_PRICE_RE = re.compile(r'(?:\$|ARS)?\s*([0-9]{1,3}(?:[\.\s][0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:\.[0-9]{2}))')

def _parse_cards_html(html_text: str, limit: int, text_only: bool = TEXT_ONLY_DEFAULT) -> List[dict]:
    items: List[dict] = []
    for m in _CARD_RE.finditer(html_text):
        href = m.group("href")
        inner = m.group("inner") or ""
        tmatch = _TITLE_RE.search(inner)
        title = (tmatch.group(1) or tmatch.group(2)) if tmatch else None
        if not title:
            txt = re.sub(r"<[^>]+>", " ", inner)
            txt = re.sub(r"\s+", " ", txt).strip()
            title = txt[:120] or "Producto Coto"
        img = ""
        if not text_only:
            im = _IMG_RE.search(inner)
            img = im.group(1) if im else ""
        pm = _PRICE_RE.search(inner)
        price_val = pm.group(1) if pm else None
        items.append(
            {
                "title": title.strip(),
                "price": f"ARS {price_val}".replace("  ", " ") if price_val else "ARS 0.00",
                "url": _abs_url(href),
                "image_url": _img_url_from_rel(img) if img else "",
                "barcode": None,
                "promotion": None,
            }
        )
        if len(items) >= limit:
            break
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

def _map_item_from_preloaded(product: dict, text_only: bool = TEXT_ONLY_DEFAULT) -> Optional[dict]:
    title = product.get("displayName") or product.get("name") or product.get("title")
    if not title:
        return None
    url = product.get("url") or product.get("pdpUrl") or product.get("link") or product.get("productUrl") or ""
    img = "" if text_only else (product.get("imageUrl") or product.get("image") or product.get("mainImage") or product.get("thumbnail") or "")
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
        "image_url": _img_url_from_rel(img) if img else "",
        "barcode": str(barcode) if barcode else None,
        "promotion": _PROMO_NORMALIZER(str(promo_raw)) if promo_raw else None,
    }

def _map_item_from_ldjson(obj: dict, text_only: bool = TEXT_ONLY_DEFAULT) -> Optional[dict]:
    title = obj.get("name") or obj.get("title")
    if not title:
        return None
    price = None
    offers = obj.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
    elif isinstance(offers, list) and offers:
        price = offers[0].get("price")
    image = "" if text_only else obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    url = obj.get("url") or ""
    return {
        "title": title.strip(),
        "price": _price_to_ars_string(price) or "ARS 0.00",
        "url": _abs_url(url),
        "image_url": _img_url_from_rel(image or "") if image else "",
        "barcode": obj.get("gtin13") or obj.get("gtin") or None,
        "promotion": None,
    }

# ===================== Search =====================

def _build_search_url(q: str) -> str:
    params = {"Ntt": q, "idSucursal": str(COTO_SUCURSAL_ID), "Dy": "1", "Nrpp": "12"}
    return f"{COTO_BASE}/sitios/cdigi/busqueda?{_url.urlencode(params)}"

def _parse_search_page(html_text: str, q: str, limit: int, text_only: bool = TEXT_ONLY_DEFAULT) -> List[dict]:
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
            mapped = _map_item_from_preloaded(p, text_only=text_only)
            if mapped and _title_seems_related(mapped["title"], q):
                items.append(mapped)
                if len(items) >= limit:
                    return items

    # 2) JSON-LD fallback
    if len(items) < limit:
        for obj in _extract_ldjson_products(html_text):
            mapped = _map_item_from_ldjson(obj, text_only=text_only)
            if mapped and _title_seems_related(mapped["title"], q):
                items.append(mapped)
                if len(items) >= limit:
                    return items

    # 3) HTML cards fallback
    if len(items) < limit:
        for it in _parse_cards_html(html_text, limit=limit - len(items), text_only=text_only):
            if _title_seems_related(it["title"], q):
                items.append(it)
                if len(items) >= limit:
                    break

    # de-dup por URL
    seen: Dict[str, dict] = {}
    out: List[dict] = []
    for it in items:
        u = it.get("url") or ""
        if u and u not in seen:
            seen[u] = it
            out.append(it)
            if len(out) >= limit:
                break
    return out

def _maybe_enrich_barcode_from_pdp(item: dict, timeout: Optional[float] = None) -> dict:
    """Si no tiene barcode, abre la PDP y lo intenta sacar del JSON-LD (rápido)."""
    if item.get("barcode") or not item.get("url"):
        return item
    try:
        html_text, _ = _get(item["url"], timeout=timeout)
        for obj in _extract_ldjson_products(html_text):
            gtin = obj.get("gtin13") or obj.get("gtin") or obj.get("gtin14") or obj.get("gtin12")
            if gtin:
                item["barcode"] = str(gtin)
                break
        return item
    except Exception:
        return item

def search_coto_requests(q: str, limit: int = TOP_N, *, fast: bool = FAST_MODE_DEFAULT, text_only: bool = TEXT_ONLY_DEFAULT, enrich_barcode: bool = ENRICH_BARCODE_DEFAULT) -> List[Dict]:
    def _one_search(skip_bootstrap: bool) -> Tuple[List[Dict], str, int]:
        if not skip_bootstrap:
            _bootstrap_cookies()
        url = _build_search_url(q)
        html_text, status = _get(url)
        items = _parse_search_page(html_text, q, limit=limit, text_only=text_only)
        return items, html_text, status

    # FAST: intenta sin bootstrap primero
    items, html_text, status = _one_search(skip_bootstrap=fast)

    blocked = ("Access Denied" in html_text) or ("captcha" in html_text.lower())
    if status >= 400 or blocked:
        log.warning(f"[COTO] status={status} blocked={blocked}")
        items = []

    # Si vacío y veníamos en FAST, reintenta con bootstrap
    if not items and fast:
        items, html_text, status = _one_search(skip_bootstrap=False)

    if not items:
        log.info(f"[COTO] 0 items en búsqueda. len={len(html_text)}")

    # opcional: enriquecer solo si se pide
    if enrich_barcode and items:
        items[0] = _maybe_enrich_barcode_from_pdp(items[0], timeout=min(_DEFAULT_TIMEOUT, 5))

    # ranking simple
    qn = _norm_text(q)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (qn in t)
        has_price = it.get("price") not in (None, "ARS 0.00")
        return (exact, has_price, -len(t))
    items.sort(key=_score, reverse=True)
    return items[:limit]

def search_coto_by_barcode(barcode: str, limit: int = TOP_N, *, fast: bool = FAST_MODE_DEFAULT, text_only: bool = TEXT_ONLY_DEFAULT, enrich_barcode: bool = ENRICH_BARCODE_DEFAULT) -> List[Dict]:
    items = search_coto_requests(str(barcode), limit=limit, fast=fast, text_only=text_only, enrich_barcode=False)
    bn = str(barcode)
    def _score(it: dict) -> tuple:
        t = _norm_text(it["title"])
        exact = (bn == (it.get("barcode") or "")) or (bn in (it.get("url") or ""))
        return (exact, bn in t, -len(t))
    items.sort(key=_score, reverse=True)
    if enrich_barcode and items and (not items[0].get("barcode") or bn not in (items[0].get("barcode") or "")):
        items[0] = _maybe_enrich_barcode_from_pdp(items[0], timeout=min(_DEFAULT_TIMEOUT, 5))
    return items[:limit]
