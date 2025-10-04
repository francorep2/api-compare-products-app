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
HARD_TIMEOUT_SEC = 11.5    # hard cutoff (útil si te envuelven con el agregador)
TOP_N = 3

# PROMO_NORMALIZER opcional; si no está en tu repo, queda no-op
try:
    from scrapers._utils_promos import PROMO_NORMALIZER  # type: ignore
except Exception:
    def PROMO_NORMALIZER(text: Optional[str]) -> Optional[str]:
        return text

# ===================== Helpers =====================
def _to_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    # Formatos: "1234,56" / "1.234,56" / "1234.56"
    s2 = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except Exception:
        return None

def _ars(amount: Optional[float]) -> Optional[str]:
    if amount is None:
        return None
    # "ARS 1.234,56"
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
    # Go: strings.Replace(RecordState, "?format=json", "", -1)
    return (record_state or "").replace("?format=json", "")

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

# ===================== Normalizer & Extractor (paridad con Go) =====================
def _normalize_response_structure(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Replica el Normalizer del Go:
    - Navega: contents[0].Main[?].contents[0].records -> CotoResponseProduct
    - Mantiene SOLO los que tienen product.dtoDescuentos (como en Go)
    - Adjunta el JSON parseado de dtoDescuentos como _cotoProductDiscounts
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
                    prod_discounts = (((raw.get("records") or [{}])[0].get("attributes") or {})
                                      .get("product.dtoDescuentos")) or []
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

def _extract_item(raw: Dict[str, Any], q: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    """
    Devuelve SearchItem dict:
      { title, price "ARS ...", url, image_url, barcode(None), promotion(None|normalizada) }
    """
    # Encabezado
    attrs = raw.get("attributes") or {}
    display = (attrs.get("product.displayName") or [])
    repo_id = (attrs.get("product.repositoryId") or [])
    title = (display[0] if display else "") or ""
    if not title or not _title_seems_related(title, q):
        return None

    # Link
    record_state = (raw.get("detailsAction") or {}).get("recordState") or ""
    url = _normalize_link(record_state)

    # Detalles del primer record
    r0_attrs = (((raw.get("records") or [{}])[0]).get("attributes") or {})
    img_arr = r0_attrs.get("product.mediumImage.url") or []
    qty_arr = r0_attrs.get("sku.quantity") or []
    active_price_arr = r0_attrs.get("sku.activePrice") or []
    ref_price_arr = r0_attrs.get("sku.referencePrice") or []

    unavailable = (str(qty_arr[0]).strip() == "0") if qty_arr else False
    if unavailable:
        return None

    list_price = _to_float(ref_price_arr[0]) if ref_price_arr else None
    active_price = _to_float(active_price_arr[0]) if active_price_arr else None
    price = active_price if active_price is not None else (list_price if list_price is not None else None)

    # dtoDescuentos (JSON adjunto)
    discounts = raw.get("_cotoProductDiscounts") or []
    promotion_text: Optional[str] = None
    if isinstance(discounts, list) and discounts:
        first = discounts[0] or {}
        desc_val = first.get("precioDescuento")
        desc_f = _to_float(str(desc_val)) if desc_val is not None else None
        if desc_f and desc_f > 0:
            price = desc_f
        # si querés exponer algo en promotion:
        # promotion_text = PROMO_NORMALIZER(json.dumps(first, ensure_ascii=False))

    image_url = img_arr[0] if img_arr else None
    # Ajustes URL relativas (poco frecuente en este endpoint)
    if image_url and isinstance(image_url, str) and image_url.startswith("//"):
        image_url = "https:" + image_url
    if url and url.startswith("/"):
        url = COTO_BASE + url

    return {
        "title": title,
        "price": _ars(price),
        "url": url or None,
        "image_url": image_url,
        "barcode": None,         # el endpoint no expone EAN; si querés podemos setear repo_id[0] como fallback
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
        # chequeo de errores tipo GraphQL (por si aparece)
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
