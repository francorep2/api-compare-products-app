# scrapers_async/dia_scraper_async.py
import re
import json
import time
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

BASE = "https://diaonline.supermercadosdia.com.ar"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BARCODE_RE = re.compile(r"^\d{8,14}$")

# ---------- HTTP client (global, keep-alive + HTTP/2) ----------
_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None

async def _get_client() -> httpx.AsyncClient:
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None:
        _HTTPX_CLIENT = httpx.AsyncClient(
            http2=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": BASE,
            },
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )
    return _HTTPX_CLIENT

# ---------- Cache in-memory ----------
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_CACHE_TTL = 90.0  # seg

def _cache_get(key: str) -> Optional[List[Dict]]:
    now = time.time()
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if now - ts <= _CACHE_TTL:
        return val
    _CACHE.pop(key, None)
    return None

def _cache_put(key: str, val: List[Dict]) -> None:
    _CACHE[key] = (time.time(), val)

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
    # ARS 1.234,56 (opcional; si preferís simple, deja "%.2f")
    p = f"{float(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"ARS {p}"

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
    return None

# ---------- VTEX mappers ----------
def _find_barcode_in_vtex_json(p: Dict) -> Optional[str]:
    try:
        items = p.get("items", [])
        if items and items[0]:
            ean = _only_digits(items[0].get("ean"))
            if ean and 8 <= len(ean) <= 14:
                return ean
            ref_ids = items[0].get("referenceId", [])
            if ref_ids and ref_ids[0]:
                ref_val = _only_digits(ref_ids[0].get("Value"))
                if ref_val and 8 <= len(ref_val) <= 14:
                    return ref_val
    except (KeyError, IndexError):
        pass
    return None

def _product_json_to_item(p: Dict) -> Dict:
    title = p.get("productName")
    price, image_url, product_id = None, None, p.get("productId")
    try:
        items = p.get("items", [])
        if items:
            image_url = items[0].get("images", [{}])[0].get("imageUrl")
            sellers = items[0].get("sellers", [])
            if sellers:
                co = sellers[0].get("commertialOffer", {}) or {}
                price = _clean_price(co.get("Price"))
    except (KeyError, IndexError):
        pass
    return {
        "title": title,
        "url": _normalize_url(p.get("link")),
        "image_url": image_url,
        "price": price,
        "barcode": _find_barcode_in_vtex_json(p),
        "promotion": None,
        "product_id": product_id,  # para GraphQL
    }

# ---------- Retries helper ----------
async def _get_json(client: httpx.AsyncClient, url: str, *, params=None, headers=None, attempts=2):
    last = None
    for i in range(attempts):
        try:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < attempts - 1:
                await httpx.AsyncClient.aclose  # no-op; evitamos lints
                await asyncio.sleep(0.3 * (i + 1))  # type: ignore
    if last:
        raise last

async def _post_json(client: httpx.AsyncClient, url: str, *, json_body=None, headers=None, attempts=2):
    last = None
    for i in range(attempts):
        try:
            r = await client.post(url, json=json_body, headers=headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < attempts - 1:
                await asyncio.sleep(0.3 * (i + 1))
    if last:
        raise last

# ---------- Search (VTEX) ----------
async def _api_search(q: str, by_ean: bool, limit: int) -> List[Dict]:
    """
    Estrategia robusta:
      1) ft=q
      2) /search/<q>
      3) ft=q con '+'
      4) fq=alternateIds_Ean:<ean> (si by_ean)
    """
    client = await _get_client()
    _from, _to = 0, max(0, limit + 2)
    referer = f"{BASE}/{quote(q)}?_q={quote(q)}&map={'ft' if not by_ean else 'specificationFilter_1'}"
    common_headers = {"Referer": referer}

    # 4) EAN directo (priorizamos si by_ean)
    if by_ean:
        try:
            data = await _get_json(
                client,
                f"{BASE}/api/catalog_system/pub/products/search",
                params={"fq": f"alternateIds_Ean:{q}", "_from": _from, "_to": _to},
                headers=common_headers,
            )
            if isinstance(data, list) and data:
                return [_product_json_to_item(p) for p in data]
        except Exception:
            pass

    # 1) ft=
    try:
        data = await _get_json(
            client,
            f"{BASE}/api/catalog_system/pub/products/search",
            params={"ft": q, "_from": _from, "_to": _to},
            headers=common_headers,
        )
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    # 2) /search/<q>
    try:
        data = await _get_json(
            client,
            f"{BASE}/api/catalog_system/pub/products/search/{quote(q)}",
            params={"_from": _from, "_to": _to},
            headers=common_headers,
        )
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    # 3) ft= con '+'
    try:
        data = await _get_json(
            client,
            f"{BASE}/api/catalog_system/pub/products/search",
            params={"ft": q.replace(" ", "+"), "_from": _from, "_to": _to},
            headers=common_headers,
        )
        if isinstance(data, list) and data:
            return [_product_json_to_item(p) for p in data]
    except Exception:
        pass

    return []

# ---------- Promos por GraphQL ----------
async def _fetch_promotions_via_graphql(product_ids: List[str]) -> Dict[str, str]:
    """
    Usa API GraphQL de VTEX para cluster highlights -> badges de promo.
    Devuelve dict { productId: "PROMO" }
    """
    if not product_ids:
        return {}

    graphql_url = f"{BASE}/_v/public/graphql/v1"
    payload = {
        "query": """
            query ProductHighlights($identifiers: [ProductUniqueIdentifier!]) {
                productHighlights(identifiers: $identifiers) {
                    productClusterHighlights { name }
                    productId
                }
            }
        """,
        "variables": {
            "identifiers": [{"field": "id", "value": str(pid)} for pid in product_ids],
        },
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": BASE + "/",
    }

    promo_map: Dict[str, str] = {}
    client = await _get_client()
    try:
        data = await _post_json(client, graphql_url, json_body=payload, headers=headers)
        items = (data or {}).get("data", {}).get("productHighlights", []) or []
        for product in items:
            pid = str(product.get("productId") or "")
            highlights = product.get("productClusterHighlights") or []
            if pid and highlights:
                txt = " | ".join(h.get("name", "") for h in highlights if h and h.get("name"))
                sp = _short_promo(txt)
                if sp:
                    promo_map[pid] = sp
    except Exception as e:
        print(f"[DIA] GraphQL promos falló: {e}")

    return promo_map

# ---------- Función principal ----------
async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    is_barcode = bool(BARCODE_RE.fullmatch(query))
    cache_key = f"dia|{query}|{limit}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    # 1) Base por API
    api_items = await _api_search(query, by_ean=is_barcode, limit=limit)
    if not api_items:
        _cache_put(cache_key, [])
        return []

    # 2) Enriquecer promos (sólo búsq. por texto)
    if not is_barcode:
        pids = [str(i.get("product_id")) for i in api_items if i.get("product_id")]
        if pids:
            promos = await _fetch_promotions_via_graphql(pids)
            if promos:
                for it in api_items:
                    pid = str(it.get("product_id") or "")
                    if pid in promos:
                        it["promotion"] = promos[pid]

    # 3) Normalización final
    final: List[Dict] = []
    seen = set()
    for it in api_items:
        it.pop("product_id", None)
        url = _normalize_url(it.get("url"))
        if url and url not in seen:
            # Si usás Pydantic:
            # final.append(SearchItem(**it).dict())
            final.append(it)
            seen.add(url)

    result = final[:limit]
    _cache_put(cache_key, result)
    return result
