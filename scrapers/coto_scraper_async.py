# scrapers_async/coto_scraper_async.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
import asyncio
import json
import re
from urllib.parse import quote
import httpx
import unicodedata

# ===================== Config =====================
COTO_BASE = "https://www.cotodigital.com.ar"
REQUEST_TIMEOUT = 8.0      # timeout por request individual
HARD_TIMEOUT_SEC = 11.5    # hard cutoff para no colgar el agregador
TOP_N = 3

# PROMO_NORMALIZER (si existe en tu repo); si no, no-op
try:
    from scrapers._utils_promos import PROMO_NORMALIZER  # type: ignore
except Exception:
    def PROMO_NORMALIZER(text: Optional[str]) -> Optional[str]:
        return text

# ===================== Helpers =====================
def _to_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s2 = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except Exception:
        return None

def _ars(amount: Optional[float]) -> Optional[str]:
    if amount is None:
        return None
    val = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"ARS {val}"

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: Optional[str]) -> bool:
    if not q:
        return True
    t = _norm_text(title)
    s = _norm_text(q)
    for w in s.split():
        if w and w not in t:
            return False
    return True

def _normalize_link(record_state: str) -> str:
    # Limpia el sufijo JSON y vuelve absoluto
    url = (record_state or "").replace("?format=json", "")
    if url.startswith("/"):
        url = COTO_BASE + url
    return url

def _fix_discount_scale(desc: Optional[float], reference: Optional[float]) -> Optional[float]:
    """
    Si el descuento viene 'escalado' (p.ej. 3.100.000.000 en lugar de 3100),
    dividimos por 10 hasta que sea razonable respecto al precio base.
    Criterio: 0 < desc_norm <= reference * 1.05
    """
    if not desc or desc <= 0 or not reference or reference <= 0:
        return None
    v = float(desc)
    for _ in range(6):  # intenta hasta /10^6
        if 0 < v <= reference * 1.05:
            return v
        v /= 10.0
    return None

def _safe_get(d: Dict[str, Any], path: List[Any], default=None):
    cur = d
    try:
        for p in path:
            cur = cur[p]
        return cur
    except Exception:
        return default

# ===================== Fetch =====================
async def _fetch_coto_json(query: str) -> Dict[str, Any]:
    url = f"{COTO_BASE}/sitios/cdigi/categoria"
    params = {"Ntt": query, "format": "json"}
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

# ===================== Normalizer (paridad con Go) =====================
def _normalize_response_structure(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Replica el Normalizer del Go:
    - Recorre contents[*].Main[*].contents[*].records[*]
    - Conserva solo los que traen product.dtoDescuentos
    - Adjunta el JSON parseado como _cotoProductDiscounts
    """
    normalized: List[Dict[str, Any]] = []
    contents = data.get("contents") or []
    if not contents:
        return normalized

    for section in contents:
        main_list = section.get("Main") or []
        for main in main_list:
            blocks = main.get("contents") or []
            for block in blocks:
                records = block.get("records") or []
                for raw in records:
                    prod_discounts = _safe_get(raw, ["records", 0, "attributes", "product.dtoDescuentos"], [])
                    if not prod_discounts or not isinstance(prod_discounts, list):
                        continue
                    try:
                        parsed = json.loads(prod_discounts[0])
                        if not isinstance(parsed, list):
                            parsed = []
                    except Exception:
                        parsed = []
                    enriched = dict(raw)
                    enriched["_cotoProductDiscounts"] = parsed
                    normalized.append(enriched)
    return normalized

# ===================== Extractor (a SearchItem-like) =====================
def _extract_item(raw: Dict[str, Any], q: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    attrs = raw.get("attributes") or {}
    display = (attrs.get("product.displayName") or [])
    repo_id = (attrs.get("product.repositoryId") or [])
    title = (display[0] if display else "") or ""
    if not title or not _title_seems_related(title, q):
        return None

    # Link limpio + absoluto
    record_state = (raw.get("detailsAction") or {}).get("recordState") or ""
    url = _normalize_link(record_state)

    # Detalles del primer record
    r0_attrs = _safe_get(raw, ["records", 0, "attributes"], {}) or {}
    img_arr = r0_attrs.get("product.mediumImage.url") or []
    qty_arr = r0_attrs.get("sku.quantity") or []
    active_price_arr = r0_attrs.get("sku.activePrice") or []
    ref_price_arr = r0_attrs.get("sku.referencePrice") or []

    unavailable = (str(qty_arr[0]).strip() == "0") if qty_arr else False
    if unavailable:
        return None

    list_price = _to_float(ref_price_arr[0]) if ref_price_arr else None
    active_price = _to_float(active_price_arr[0]) if active_price_arr else None
    base_price = active_price if active_price is not None else (list_price if list_price is not None else None)

    # dtoDescuentos (JSON ya parseado en el normalizador)
    discounts = raw.get("_cotoProductDiscounts") or []
    promotion_text: Optional[str] = None
    final_price = base_price

    if isinstance(discounts, list) and discounts:
        first = discounts[0] or {}
        desc_val = first.get("precioDescuento")
        desc_f = _to_float(str(desc_val)) if desc_val is not None else None
        desc_norm = _fix_discount_scale(desc_f, base_price)
        if desc_norm is not None and (final_price is None or desc_norm <= final_price):
            final_price = desc_norm
            ref = base_price or 0.0
            if ref > 0:
                pct = max(0.0, 100.0 * (1.0 - (final_price / ref)))
                promotion_text = f"{pct:.0f}% OFF"

    image_url = img_arr[0] if img_arr else None
    if image_url and isinstance(image_url, str) and image_url.startswith("//"):
        image_url = "https:" + image_url

    # Fallbacks pedidos:
    # - barcode estable (NO EAN): repositoryId; si preferís un hash del título, podés cambiarlo.
    barcode = repo_id[0] if repo_id else None
    # import hashlib; barcode = hashlib.md5(title.encode("utf-8")).hexdigest()[:13]  # alternativa

    return {
        "title": title,
        "price": _ars(final_price),
        "url": url or None,
        "image_url": image_url,
        "barcode": barcode,
        "promotion": PROMO_NORMALIZER(promotion_text) if promotion_text else None,
    }

# ===================== Entry point (tu firma) =====================
async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict[str, Optional[str]]]:
    """
    Firma compatible con tus otros scrapers:
      - q: texto
      - barcode: si viene, se usa como query
      - limit: TOP N
    Retorna: List[SearchItem dict] con keys: title, price("ARS ..."), url, image_url, barcode, promotion
    """
    query = (barcode or q or "").strip()
    if not query:
        return []

    async def _run():
        data = await _fetch_coto_json(quote(query, safe=""))

        # si la API devolviera errors (GraphQL-like), devolvemos vacío
        if isinstance(data, dict) and data.get("errors"):
            return []

        normalized = _normalize_response_structure(data)
        items: List[Dict[str, Optional[str]]] = []
        for raw in normalized:
            it = _extract_item(raw, query)
            if it:
                items.append(it)
                if len(items) >= (limit or TOP_N):
                    break
        return items

    try:
        return await asyncio.wait_for(_run(), timeout=HARD_TIMEOUT_SEC)
    except Exception:
        return []
