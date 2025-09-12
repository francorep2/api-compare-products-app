# app/services/vtex_price_http.py
from typing import List, Dict, Optional
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

def _price_from_jsonld_html(html: str) -> Optional[str]:
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
                    return f"ARS {v:.2f}"
    return None

def _price_from_meta_or_text(html: str) -> Optional[str]:
    for m in re.findall(
        r'<meta[^>]+(?:itemprop=["\']price["\']|property=["\']product:price:amount["\'])[^>]+content=["\']([^"\']+)["\']',
        html, flags=re.I
    ):
        v = _normnum(m)
        if v is not None:
            return f"ARS {v:.2f}"
    # DOM textual
    any_txt = re.sub(r"\s+", " ", html)
    matches = re.findall(r"(?:\$|ARS)\s*[\d\. ]+(?:,\d{2})?", any_txt, flags=re.I)
    best = None
    for s in matches:
        v = _normnum(s)
        if v is not None and (best is None or v > best):
            best = v
    if best is not None:
        return f"ARS {best:.2f}"
    # skuJson bestPrice
    m = re.search(r"skuJson\s*=\s*({.*?});", html, flags=re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            for sk in (data.get("skus") or []):
                bp = sk.get("bestPrice")
                if bp:
                    return f"ARS {float(bp)/100:.2f}"
        except:
            pass
    return None

async def _fetch_html(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=18)) as r:
            if r.status != 200:
                return None
            return await r.text()
    except:
        return None

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
        for html, i in zip(pages, idx_map):
            if isinstance(html, Exception) or not html:
                continue
            price = _price_from_jsonld_html(html) or _price_from_meta_or_text(html)
            if price:
                items[i]["price"] = price
    return items
