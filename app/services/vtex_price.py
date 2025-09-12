# app/services/vtex_price.py
from typing import List, Dict, Optional
import json, re, unicodedata
from app.core.browser import browser_manager

def _clean(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def _normnum(s: str) -> Optional[float]:
    try:
        s = s.replace(".", "").replace(",", ".")
        return float(re.sub(r"[^\d.]", "", s))
    except: return None

async def _price_from_jsonld(page) -> Optional[str]:
    try:
        scripts = await page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "nodes => nodes.map(n => n.textContent)"
        )
    except: scripts = []
    for raw in scripts or []:
        try:
            data = json.loads(raw)
        except: 
            continue
        stack = data if isinstance(data, list) else [data]
        for obj in stack:
            if not isinstance(obj, dict): 
                continue
            t = obj.get("@type")
            if (isinstance(t, str) and "Product" in t) or (isinstance(t, list) and any("Product" in x for x in t)):
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

async def _price_from_meta_or_dom(page) -> Optional[str]:
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        el = page.locator(sel).first
        if await el.count():
            raw = await el.get_attribute("content")
            if raw:
                v = _normnum(raw)
                if v is not None:
                    return f"ARS {v:.2f}"
    conts = ["#skuBestPrice",".skuBestPrice",".best-price",".bestPrice",".vtex-product-price-1-x-sellingPriceValue",".price",".new-price"]
    for sel in conts:
        el = page.locator(sel).first
        if await el.count():
            txt = _clean(await el.inner_text())
            if txt:
                m = re.findall(r"(?:\$|ARS)\s*[\d\. ]+(?:,\d{2})?", txt, flags=re.I)
                for s in m:
                    v = _normnum(s)
                    if v is not None:
                        return f"ARS {v:.2f}"
    any_txt = _clean(await page.inner_text("body"))
    if any_txt:
        m = re.findall(r"(?:\$|ARS)\s*[\d\. ]+(?:,\d{2})?", any_txt, flags=re.I)
        best = None
        for s in m:
            v = _normnum(s)
            if v is not None and (best is None or v > best):
                best = v
        if best is not None:
            return f"ARS {best:.2f}"
    try:
        has = await page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if has:
            data = await page.evaluate("() => window.skuJson")
            if isinstance(data, dict):
                for s in (data.get("skus") or []):
                    bp = s.get("bestPrice")
                    if bp:
                        return f"ARS {float(bp)/100:.2f}"
    except: pass
    return None

async def enrich_vtex_prices(items: List[Dict]) -> List[Dict]:
    if not items: 
        return items
    async with browser_manager.new_context(locale="es-AR", viewport={"width":1366,"height":800}) as ctx:
        for it in items:
            if it.get("price"): 
                continue
            url = it.get("url")
            if not url: 
                continue
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                price = await _price_from_jsonld(page)
                if not price:
                    price = await _price_from_meta_or_dom(page)
                if price:
                    it["price"] = price
            except:
                pass
            finally:
                try: await page.close()
                except: pass
    return items
