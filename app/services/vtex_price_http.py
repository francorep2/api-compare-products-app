# app/services/vtex_price_http.py
from typing import List, Dict, Optional, Tuple
import aiohttp, asyncio, json, re

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "es-AR,es;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
}

def _normnum(s: str) -> Optional[float]:
    try:
        s = s.replace(".", "").replace(",", ".")
        return float(re.sub(r"[^\d.]", "", s))
    except:
        return None

def _price_from_jsonld_html(html: str) -> Optional[float]:
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, flags=re.I | re.S
    ):
        try:
            data = json.loads(raw)
        except:
            continue
        stack = data if isinstance(data, list) else [data]
        for obj in stack:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            is_product = (
                (isinstance(t, str) and "Product" in t) or
                (isinstance(t, list) and any("Product" in x for x in t))
            )
            if not is_product:
                continue
            offers = obj.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                p = offers.get("price") or offers.get("lowPrice")
                if p is None:
                    continue
                v = _normnum(str(p))
                if v is not None:
                    return v
    return None

def _best_text_price(html: str) -> Optional[float]:
    for m in re.findall(
        r'<meta[^>]+(?:itemprop=["\']price["\']|property=["\']product:price:amount["\'])[^>]+content=["\']([^"\']+)["\']',
        html, flags=re.I
    ):
        v = _normnum(m)
        if v is not None:
            return v
    any_txt = re.sub(r"\s+", " ", html)
    matches = re.findall(r"(?:\$|ARS)\s*[\d\. ]+(?:,\d{2})?", any_txt, flags=re.I)
    best = None
    for s in matches:
        v = _normnum(s)
        if v is not None and (best is None or v > best):
            best = v
    return best

def _skujson_best_price(html: str, ref: Optional[float]) -> Optional[float]:
    m = re.search(r"skuJson\s*=\s*({.*?});", html, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except:
        return None
    candidates: list[float] = []
    for sk in (data.get("skus") or []):
        bp = sk.get("bestPrice")
        if bp is None:
            continue
        try:
            bp = float(bp)
        except:
            continue
        for x in (bp, bp/100.0, bp/10.0, bp*100.0):
            if x > 0:
                candidates.append(x)
    if not candidates:
        return None
    target: Optional[float] = ref
    if ref is not None and ref < 50:
        target = ref * 100.0
    candidates = [c for c in candidates if c >= 50]
    if not candidates:
        return None
    if target is not None:
        return min(candidates, key=lambda c: abs(c - target))
    return max(candidates)

def _state_spot_and_price(html: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(r"window\.__STATE__\s*=\s*({.*?});", html, flags=re.S)
    if not m:
        return (None, None)
    try:
        state = json.loads(m.group(1))
    except:
        return (None, None)

    spot_best: Optional[float] = None
    price_best: Optional[float] = None

    def upd(val: Optional[float], current: Optional[float], pick="min") -> Optional[float]:
        if val is None or val <= 0:
            return current
        if current is None:
            return val
        if pick == "min":
            return val if val < current else current
        return val if val > current else current

    def walk(o):
        nonlocal spot_best, price_best
        if isinstance(o, dict):
            if "commertialOffer" in o and isinstance(o["commertialOffer"], dict):
                co = o["commertialOffer"]
                sp = co.get("spotPrice")
                pr = co.get("Price")
                try:
                    if sp is not None:
                        sp = float(sp)
                except: sp = None
                try:
                    if pr is not None:
                        pr = float(pr)
                except: pr = None
                spot_best = upd(sp, spot_best, "min")
                price_best = upd(pr, price_best, "min")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(state)
    return (spot_best, price_best)

async def _fetch_html(session: aiohttp.ClientSession, url: str) -> Optional[Tuple[str, str]]:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=18)) as r:
            if r.status != 200:
                return None
            return (str(r.url), await r.text())
    except:
        return None

def _fmt(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    return f"ARS {v:.2f}"

async def enrich_vtex_prices_http(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []
        idx_map = []
        for i, it in enumerate(items):
            if it.get("price"):
                continue
            url = it.get("url")
            if not url:
                continue
            tasks.append(_fetch_html(session, url))
            idx_map.append(i)
        if not tasks:
            return items
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        for got, i in zip(pages, idx_map):
            if isinstance(got, Exception) or not got:
                continue
            url, html = got
            ref = _price_from_jsonld_html(html) or _best_text_price(html)
            sp, pr = _state_spot_and_price(html)
            sku = _skujson_best_price(html, ref)

            final: Optional[float] = None

            if "carrefour.com.ar" in (url or ""):
                # Carrefour: preferir spotPrice; si no hay, Price; luego sku; refs últimas
                if sp and sp >= 50:
                    final = sp
                elif pr and pr >= 50:
                    final = pr
                elif sku:
                    final = sku
                elif ref and ref >= 50:
                    final = ref
                elif ref and 5 <= ref < 50:
                    final = ref * 100.0
            else:
                if ref is not None and ref >= 50:
                    final = ref
                elif sp and sp >= 50:
                    final = sp
                elif pr and pr >= 50:
                    final = pr
                elif sku:
                    final = sku
                elif ref and 5 <= ref < 50:
                    final = ref * 100.0

            if final is not None:
                items[i]["price"] = _fmt(final)
    return items
