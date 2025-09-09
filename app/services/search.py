# app/services/search.py
from typing import Dict, List, Optional, Callable, Any, Tuple, Awaitable, Union
import asyncio
import inspect
import traceback
import anyio

# --- Async HTTP preferido; fallback a requests en worker (mantiene API async) ---
try:
    import httpx  # type: ignore
    _HAS_HTTPX = True
except Exception:
    _HAS_HTTPX = False
    import requests  # type: ignore

from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo

# ---------- Config ----------
StoreFunc = Union[
    Callable[..., Awaitable[List[Dict[str, Any]]]],
    Callable[..., List[Dict[str, Any]]]
]

STORES: Dict[str, StoreFunc] = {
    "coto": search_coto,
    "carrefour": search_carrefour,
    "dia": search_dia,
    "jumbo": search_jumbo,
}

VTEX_BASES: Dict[str, str] = {
    "carrefour": "https://www.carrefour.com.ar",
    "dia": "https://diaonline.supermercadosdia.com.ar",
    "jumbo": "https://www.jumbo.com.ar",
    # coto no es VTEX
}

DEFAULT_PER_STORE_TIMEOUT = 30  # seg por tienda
DEFAULT_OVERALL_TIMEOUT = 60    # seg tope general

HTTP_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Referer": "https://www.google.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
}

# ---------- Utils ----------
def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": item.get("title"),
        "price": item.get("price"),
        "url": item.get("url"),
        "image_url": item.get("image_url"),
        "barcode": item.get("barcode"),
    }

def _filter_by_barcode_field(items: List[Dict[str, Any]], barcode: str) -> List[Dict[str, Any]]:
    return [i for i in items if (i.get("barcode") and str(i["barcode"]) == barcode)]

def _contains_barcode_in_any_link(i: Dict[str, Any], barcode: str) -> bool:
    for k in ("url", "image_url"):
        v = (i.get(k) or "")
        if isinstance(v, str) and barcode in v:
            return True
    return False

def _filter_relaxed_with_links(items: List[Dict[str, Any]], barcode: str) -> List[Dict[str, Any]]:
    return [i for i in items if _contains_barcode_in_any_link(i, barcode)]

def _ean_candidates(raw: str) -> List[str]:
    s = "".join(ch for ch in str(raw) if ch.isdigit())
    cands = [s]
    if len(s) == 13 and s[0] == "0":
        cands.append(s[1:])
    elif len(s) == 12:
        cands.append("0" + s)
    seen, uniq = set(), []
    for x in cands:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def _first(x: Any):
    return x[0] if isinstance(x, list) and x else None

def _safe_price_from_sku(sku: Dict[str, Any]) -> Optional[float]:
    try:
        sellers = sku.get("Sellers") or sku.get("sellers") or []
        seller0 = _first(sellers) or {}
        offer = seller0.get("CommertialOffer") or seller0.get("commertialOffer") or {}
        price = offer.get("Price")
        return float(price) if price is not None else None
    except Exception:
        return None

# ---------- Async HTTP helpers ----------
async def _fetch_json(url: str, timeout: float = 12.0) -> Any:
    if _HAS_HTTPX:
        async with httpx.AsyncClient(timeout=timeout, headers=HTTP_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
    else:
        r = await anyio.to_thread.run_sync(lambda: requests.get(url, headers=HTTP_HEADERS, timeout=timeout))  # type: ignore
        if r.status_code != 200:
            return None
        return r.json()

async def _vtex_products_search(base: str, fq: str, timeout: float) -> Any:
    url = f"{base.rstrip('/')}/api/catalog_system/pub/products/search/?fq={fq}"
    return await _fetch_json(url, timeout=timeout)

async def _vtex_products_search_ft(base: str, term: str, timeout: float, sc: Optional[int] = None) -> Any:
    base_url = f"{base.rstrip('/')}/api/catalog_system/pub/products/search/"
    url = f"{base_url}?ft={term}" if sc is None else f"{base_url}?ft={term}&sc={sc}"
    return await _fetch_json(url, timeout=timeout)

async def _vtex_sku_by_ean(base: str, ean: str, timeout: float) -> Any:
    url = f"{base.rstrip('/')}/api/catalog_system/pub/sku/stockkeepingunitbyean/{ean}"
    return await _fetch_json(url, timeout=timeout)

async def _vtex_products_by_product_id(base: str, product_id: Union[int, str], timeout: float) -> Any:
    url = f"{base.rstrip('/')}/api/catalog_system/pub/products/search/?fq=productId:{product_id}"
    return await _fetch_json(url, timeout=timeout)

async def _vtex_variations_by_product_id(base: str, product_id: Union[int, str], timeout: float) -> Any:
    url = f"{base.rstrip('/')}/api/catalog_system/pub/products/variations/{product_id}"
    return await _fetch_json(url, timeout=timeout)

def _normalize_vtex_product(base: str, p: Dict[str, Any], searched_ean: str) -> Dict[str, Any]:
    name = p.get("productName") or p.get("productTitle") or p.get("productNameComplete")
    link_text = p.get("linkText")
    product_url = f"{base.rstrip('/')}/{link_text}/p" if link_text else None
    items = p.get("items") or []
    sku0 = _first(items) or {}
    images = sku0.get("images") or []
    img0 = _first(images) or {}
    img_url = img0.get("imageUrl")
    ean_value = sku0.get("ean")
    if not ean_value:
        ref_ids = sku0.get("referenceId") or []
        ref0 = _first(ref_ids) or {}
        ean_value = ref0.get("Value") or ref0.get("value")
    price = _safe_price_from_sku(sku0)
    return {
        "title": name,
        "price": price,
        "url": product_url,
        "image_url": img_url,
        "barcode": ean_value or searched_ean,
    }

def _normalize_vtex_from_sku_and_product(base: str, sku_obj: Dict[str, Any], product_obj: Dict[str, Any], searched_ean: str) -> Dict[str, Any]:
    name = sku_obj.get("NameComplete") or product_obj.get("productName")
    images = sku_obj.get("Images") or []
    img0 = _first(images) or {}
    img_url = img0.get("ImageUrl") or img0.get("imageUrl")
    link_text = product_obj.get("linkText")
    product_url = f"{base.rstrip('/')}/{link_text}/p" if link_text else None
    ean_value = sku_obj.get("Ean") or sku_obj.get("ean") or searched_ean
    price = _safe_price_from_sku(sku_obj)
    return {
        "title": name,
        "price": price,
        "url": product_url,
        "image_url": img_url,
        "barcode": ean_value,
    }

async def _vtex_find_by_ean(base: str, ean: str, limit: int, timeout: float, dbg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Estrategia escalonada VTEX:
      0) products/search?ft=EAN (sc None/1/2) + validación por variaciones
      1) sku/stockkeepingunitbyean/{EAN}
      2) products/search?fq=alternateIds_Ean:{EAN}
      3) products/search?fq=alternateIds_RefId:{EAN}
      4) products/search?fq=skuEan:{EAN}
    """
    # 0) full-text ?ft=
    hits_ft: List[Dict[str, Any]] = []
    flags: Dict[str, bool] = {}
    for sc in (None, 1, 2):
        ps = await _vtex_products_search_ft(base, ean, timeout=timeout, sc=sc)
        key = f"ft_ok_sc{sc}" if sc is not None else "ft_ok"
        flags[key] = bool(ps)
        if not ps:
            continue
        for p in ps:
            pid = p.get("productId") or p.get("productID") or p.get("ProductId")
            ean_confirmed = False
            items = p.get("items") or []
            for it in items:
                if (it.get("ean") and str(it["ean"]) == ean):
                    ean_confirmed = True
                    break
                for ref in (it.get("referenceId") or []):
                    val = ref.get("Value") or ref.get("value")
                    if val and str(val) == ean:
                        ean_confirmed = True
                        break
                if ean_confirmed:
                    break
            if not ean_confirmed and pid:
                vars_ = await _vtex_variations_by_product_id(base, pid, timeout=timeout)
                if vars_:
                    skus = vars_.get("skus") or []
                    for sku in skus:
                        e = sku.get("skuEan") or sku.get("ean") or sku.get("Ean")
                        if e and str(e) == ean:
                            hits_ft.append(_normalize_vtex_from_sku_and_product(base, sku, p, ean))
                            ean_confirmed = True
                            break
            if ean_confirmed and len(hits_ft) < limit:
                if not hits_ft or hits_ft[-1].get("url") != (f"{base.rstrip('/')}/{p.get('linkText')}/p" if p.get("linkText") else None):
                    hits_ft.append(_normalize_vtex_product(base, p, ean))
            if len(hits_ft) >= limit:
                break
        if len(hits_ft) >= limit:
            break
    if dbg is not None:
        dbg.update(flags)
        dbg["ft_hits"] = len(hits_ft)
    if hits_ft:
        return hits_ft[:limit]

    # 1) sku/stockkeepingunitbyean
    sku = await _vtex_sku_by_ean(base, ean, timeout)
    if dbg is not None:
        dbg["sku_by_ean_ok"] = bool(sku)
    out: List[Dict[str, Any]] = []
    if sku:
        if isinstance(sku, list):
            sku = _first(sku) or {}
        pid = sku.get("ProductId") or sku.get("ProductID") or sku.get("productId")
        if pid:
            ps = await _vtex_products_by_product_id(base, pid, timeout)
            if ps:
                p0 = _first(ps) or {}
                out.append(_normalize_vtex_from_sku_and_product(base, sku, p0, ean))
                return out[:limit]
        name = sku.get("NameComplete") or sku.get("Name")
        images = sku.get("Images") or []
        img0 = _first(images) or {}
        img_url = img0.get("ImageUrl") or img0.get("imageUrl")
        price = _safe_price_from_sku(sku)
        out.append({
            "title": name,
            "price": price,
            "url": None,
            "image_url": img_url,
            "barcode": sku.get("Ean") or sku.get("ean") or ean,
        })
        return out[:limit]

    # 2-4) products search por campos alternativos
    ps = await _vtex_products_search(base, f"alternateIds_Ean:{ean}", timeout)
    if dbg is not None:
        dbg["altIds_Ean_ok"] = bool(ps)
    if ps:
        for p in ps[:limit]:
            out.append(_normalize_vtex_product(base, p, ean))
        if out:
            return out[:limit]

    pr = await _vtex_products_search(base, f"alternateIds_RefId:{ean}", timeout)
    if dbg is not None:
        dbg["altIds_RefId_ok"] = bool(pr)
    if pr:
        for p in pr[:limit]:
            out.append(_normalize_vtex_product(base, p, ean))
        if out:
            return out[:limit]

    pe = await _vtex_products_search(base, f"skuEan:{ean}", timeout)
    if dbg is not None:
        dbg["skuEan_ok"] = bool(pe)
    if pe:
        for p in pe[:limit]:
            out.append(_normalize_vtex_product(base, p, ean))
        if out:
            return out[:limit]

    return []

# ---------- Core caller (runs sync store fns in a worker) ----------
async def _call_store_once(
    fn: StoreFunc,
    *,
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    per_store_timeout: int,
    want_raw: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    diag: Dict[str, Any] = {"error": None, "trace": None, "raw_count": 0}
    raw_items: Any = None

    async def _runner_sync():
        nonlocal raw_items
        out = await anyio.to_thread.run_sync(lambda: fn(q=q, barcode=barcode, limit=limit))
        raw_items = out or []
        return [_normalize_item(x) for x in (out or [])]

    async def _runner_async():
        nonlocal raw_items
        out = await fn(q=q, barcode=barcode, limit=limit)  # type: ignore[misc]
        raw_items = out or []
        return [_normalize_item(x) for x in (out or [])]

    try:
        if inspect.iscoroutinefunction(fn):
            items = await asyncio.wait_for(_runner_async(), timeout=per_store_timeout)
        else:
            items = await asyncio.wait_for(_runner_sync(), timeout=per_store_timeout)
        diag["raw_count"] = len(raw_items)
        if want_raw:
            diag["raw_items"] = raw_items
        return items[:limit], diag
    except asyncio.TimeoutError:
        diag["error"] = f"timeout({per_store_timeout}s)"
        return [], diag
    except Exception as ex:
        diag["error"] = str(ex)
        diag["trace"] = traceback.format_exc(limit=3)
        return [], diag

# ---------- Parallel engine ----------
async def _search_all_parallel(
    *,
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    mode: str,  # "text" | "barcode"
    per_store_timeout: int = DEFAULT_PER_STORE_TIMEOUT,
    overall_timeout: int = DEFAULT_OVERALL_TIMEOUT,
    debug: int = 0,
    _text_fallback: bool = False,  # sólo en modo barcode
) -> Dict[str, Any]:

    async def handle_store_text(store_name: str, fn: StoreFunc, want_raw: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        items_text, d = await _call_store_once(
            fn, q=q, barcode=None, limit=limit, per_store_timeout=per_store_timeout, want_raw=want_raw
        )
        dbg = {"store": store_name, "phase": "text", "notes": [f"text_raw={d.get('raw_count')}"]}
        if d.get("error"):
            dbg["error"] = d["error"]; dbg["trace"] = d.get("trace")
        if want_raw:
            dbg["raw_text"] = d.get("raw_items")
        return items_text, dbg

    async def handle_store_barcode(store_name: str, fn: StoreFunc, want_raw: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        dbg: Dict[str, Any] = {"store": store_name, "phase": None, "notes": []}

        # A) VTEX HTTP (sin Playwright)
        if store_name in VTEX_BASES and barcode:
            _dbg_http: Dict[str, Any] = {}
            try:
                items_http = await asyncio.wait_for(
                    _vtex_find_by_ean(VTEX_BASES[store_name], barcode, limit, per_store_timeout, _dbg_http),
                    timeout=per_store_timeout,
                )
            except asyncio.TimeoutError:
                items_http = []
                dbg["error_phase_A_http"] = f"timeout({per_store_timeout}s)"
            except Exception as ex:
                items_http = []
                dbg["error_phase_A_http"] = str(ex)
            dbg["notes"].append(f"http_dbg={_dbg_http}")
            if items_http:
                dbg["phase"] = "barcode_http_vtex"
                dbg["notes"].append(f"http_hits={len(items_http)}")
                return items_http[:limit], dbg
            else:
                dbg["notes"].append("http_hits=0")

        # B) Strict vía service con barcode (Playwright dentro del service si hace falta)
        items_strict, d_strict = await _call_store_once(
            fn, q=None, barcode=barcode, limit=limit, per_store_timeout=per_store_timeout, want_raw=want_raw
        )
        dbg["notes"].append(f"strict_raw={d_strict.get('raw_count')}")
        if d_strict.get("error"):
            dbg["error_phase_strict"] = d_strict["error"]; dbg["trace_strict"] = d_strict.get("trace")
        if items_strict:
            exact = _filter_by_barcode_field(items_strict, barcode or "")
            if exact:
                dbg["phase"] = "barcode_store_strict"
                if want_raw: dbg["raw_strict"] = d_strict.get("raw_items")
                return exact[:limit], dbg
            relaxed = _filter_relaxed_with_links(items_strict, barcode or "")
            if relaxed:
                dbg["phase"] = "barcode_store_strict_link"
                if want_raw: dbg["raw_strict"] = d_strict.get("raw_items")
                return relaxed[:limit], dbg
            dbg["phase"] = "barcode_store_strict_unfiltered"
            if want_raw: dbg["raw_strict"] = d_strict.get("raw_items")
            return items_strict[:limit], dbg

        # C) Fallback por texto (opcional)
        if not _text_fallback:
            dbg["phase"] = "barcode_strict_nohits_no_fallback"
            return [], dbg

        tried = []
        items_b_total: List[Dict[str, Any]] = []
        raw_b_all: List[Any] = [] if want_raw else None

        for cand in _ean_candidates(barcode or ""):
            tried.append(cand)
            items_b, d_b = await _call_store_once(
                fn, q=cand, barcode=None, limit=limit, per_store_timeout=per_store_timeout, want_raw=want_raw
            )
            dbg["notes"].append(f"text('{cand}')_raw={d_b.get('raw_count')}")
            if d_b.get("error"):
                dbg.setdefault("errors_B", []).append({cand: d_b["error"]})
            if want_raw and d_b.get("raw_items") is not None:
                raw_b_all.extend(d_b["raw_items"])
            for it in items_b:
                if len(items_b_total) < limit:
                    items_b_total.append(it)

        if items_b_total:
            relaxed2 = _filter_relaxed_with_links(items_b_total, (barcode or ""))
            if relaxed2:
                dbg["phase"] = "barcode_relaxed_link"
                dbg["notes"].append(f"candidates_tried={tried}")
                if want_raw: dbg["raw_B"] = raw_b_all
                return relaxed2[:limit], dbg
            dbg["phase"] = "barcode_relaxed_unfiltered"
            dbg["notes"].append(f"candidates_tried={tried}")
            if want_raw: dbg["raw_B"] = raw_b_all
            return items_b_total[:limit], dbg

        dbg["phase"] = "no_hits"
        if want_raw:
            dbg["raw_B"] = raw_b_all
        return [], dbg

    async def _run_all() -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
        want_raw = (debug >= 2)
        tasks = []
        if mode == "text":
            for name, fn in STORES.items():
                tasks.append(handle_store_text(name, fn, want_raw))
        else:
            for name, fn in STORES.items():
                tasks.append(handle_store_barcode(name, fn, want_raw))
        results = await asyncio.gather(*tasks, return_exceptions=False)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        debugs: List[Dict[str, Any]] = []
        for (store_name, _), (items, dbg) in zip(STORES.items(), results):
            grouped[store_name] = items
            if debug:
                debugs.append(dbg)
        return grouped, debugs

    try:
        grouped, debugs = await asyncio.wait_for(_run_all(), timeout=overall_timeout)
    except asyncio.TimeoutError:
        grouped = {name: [] for name in STORES.keys()}
        debugs = [{"store": name, "phase": "timeout_all"} for name in STORES.keys()] if debug else []

    total = sum(len(v) for v in grouped.values())
    payload: Dict[str, Any] = {"count": total, "results": grouped}
    if debug:
        payload["debug"] = debugs
    return payload

# ---------- Public API ----------
async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,  # ignorado (modo texto histórico)
    limit: int = 3,
) -> Dict[str, Any]:
    return await _search_all_parallel(
        q=q, barcode=None, limit=limit, mode="text",
        per_store_timeout=DEFAULT_PER_STORE_TIMEOUT,
        overall_timeout=DEFAULT_OVERALL_TIMEOUT,
        debug=0,
    )

async def search_all_grouped_barcode(
    barcode: str,
    limit: int = 3,
    *,
    per_store_timeout: int = DEFAULT_PER_STORE_TIMEOUT,
    overall_timeout: int = DEFAULT_OVERALL_TIMEOUT,
    debug: int = 0,
    text_fallback: bool = False,   # apagado por defecto
) -> Dict[str, Any]:
    return await _search_all_parallel(
        q=None,
        barcode=barcode,
        limit=limit,
        mode="barcode",
        per_store_timeout=per_store_timeout,
        overall_timeout=overall_timeout,
        debug=debug,
        _text_fallback=text_fallback,
    )
