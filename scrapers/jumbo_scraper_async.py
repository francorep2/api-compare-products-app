# scrapers/jumbo_scraper_async.py
# -*- coding: utf-8 -*-
"""
Scraper JUMBO (VTEX) – optimizado
Estrategia:
  1) EAN → API: /api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}
  2) Texto → API: /api/catalog_system/pub/products/search?ft={q} (fallback: /search/{q})
  3) Fallback DOM light + __STATE__ si API no trae resultados
  4) Dedupe/normalización y limit al final
  5) (Opcional) Enrich PDP con cache + concurrencia limitada

Requiere:
  - Playwright BrowserContext asíncrono `ctx`
  - app.models.search_item.SearchItem
"""

import re
import json
import time
import asyncio
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from app.models.search_item import SearchItem

BASE = "https://www.jumbo.com.ar"

# ---------------------------
# Utils básicos
# ---------------------------
BARCODE_RE = re.compile(r"^\d{8,14}$")  # ean8/12/13/14
DIGITS_ANY = re.compile(r"\d+")

def _only_digits(s: Optional[str]) -> Optional[str]:
    if not s: return None
    d = re.sub(r"\D", "", s)
    return d or None

def _normalize_title(t: Optional[str]) -> Optional[str]:
    if not t: return None
    t = re.sub(r"\s+", " ", t).strip()
    return t or None

def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u: return None
    try:
        if u.startswith("/"):
            u = f"{BASE}{u}"
        p = urlparse(u)
        scheme = "https"
        netloc = p.netloc.lower()
        path = p.path or ""
        if path.endswith("/p/"):
            path = path[:-1]
        q = [
            (k, v)
            for (k, v) in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content","gclid","_ga","sc"}
        ]
        return urlunparse((scheme, netloc, path, "", urlencode(q), ""))
    except Exception:
        return u

def _clean_price_to_ars(val: Optional[str | float | int]) -> Optional[str]:
    if val is None: return None
    if isinstance(val, (int, float)):
        return f"ARS {float(val):.2f}"
    t = str(val).strip().replace("\xa0"," ").replace("\u00a0"," ")
    t = t.replace("$","").replace("ARS","").strip()
    t = t.replace(".","").replace(",",".")
    t = re.sub(r"[^\d.]", "", t)
    if not t: return None
    try:
        return f"ARS {float(t):.2f}"
    except ValueError:
        return None

def _dedupe_and_trim(items: List[Dict], limit: int) -> List[Dict]:
    seen = set()
    out: List[Dict] = []
    for it in items:
        title = _normalize_title(it.get("title"))
        url   = _normalize_url(it.get("url"))
        if not title or not url:
            continue
        bc = _only_digits(it.get("barcode"))
        key = (url, title.lower(), bc or "")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "url": url,
            "image_url": it.get("image_url") or None,
            "price": it.get("price") or None,
            "barcode": bc,
            "promotion": it.get("promotion") or None,
        })
        if len(out) >= limit:
            break
    return out

# ---------------------------
# Parse JSON VTEX
# ---------------------------
def _find_barcode_in_product(p: Dict) -> Optional[str]:
    try:
        items = p.get("items") or []
        if items:
            # referenceId / referenceIds
            refids = items[0].get("referenceId") or items[0].get("referenceIds") or []
            for r in refids:
                v = r.get("Value") or r.get("value")
                d = _only_digits(v)
                if d and 8 <= len(d) <= 14:
                    return d
            # ean directo
            ean = _only_digits(items[0].get("ean") or items[0].get("EAN"))
            if ean and 8 <= len(ean) <= 14:
                return ean
    except Exception:
        pass
    # búsqueda bruta
    try:
        txt = json.dumps(p, ensure_ascii=False)
        for m in DIGITS_ANY.finditer(txt):
            d = m.group(0)
            if 8 <= len(d) <= 14:
                return d
    except Exception:
        pass
    return None

def _product_to_item(p: Dict) -> Dict:
    title = p.get("productName") or p.get("productTitle") or p.get("productNameWithBrand")
    slug  = p.get("linkText")
    url   = p.get("link") or (f"{BASE}/{slug}/p" if slug else None)

    # imagen
    image_url = None
    try:
        items = p.get("items") or []
        imgs = (items[0].get("images") if items else []) or []
        image_url = (imgs[0].get("imageUrl") if imgs else None)
    except Exception:
        pass

    # precio
    price = None
    try:
        sellers = (p.get("items") or [])[0].get("sellers") or []
        co = (sellers[0].get("commertialOffer") if sellers else {}) or {}
        if "Price" in co:
            price = _clean_price_to_ars(co["Price"])
    except Exception:
        pass

    # promo (si viene)
    promotion = None
    try:
        teasers = (p.get("items") or [])[0].get("sellers", [{}])[0].get("commertialOffer", {}).get("Teasers") or []
        for t in teasers:
            nm = t.get("name")
            if nm:
                promotion = nm
                break
    except Exception:
        pass

    # barcode
    barcode = _find_barcode_in_product(p)

    return {
        "title": title,
        "url": url,
        "image_url": image_url,
        "price": price,
        "barcode": barcode,
        "promotion": promotion,
    }

# ---------------------------
# API VTEX helpers
# ---------------------------
async def _api_get(ctx, url: str, params: Optional[Dict] = None) -> Optional[List[Dict]]:
    """GET con ctx.request; si no existe, fetch desde una page (evita CORS)."""
    if hasattr(ctx, "request") and ctx.request:
        try:
            resp = await ctx.request.get(url, params=params or {}, timeout=25000)
            if not resp.ok:
                return None
            return await resp.json()
        except Exception:
            return None
    # Fallback fetch desde el navegador
    page = await ctx.new_page()
    try:
        await page.goto(BASE, timeout=15000)
        res = await page.evaluate(
            """async (u, p) => {
                const url = new URL(u);
                if (p) Object.entries(p).forEach(([k,v]) => url.searchParams.set(k, v));
                const r = await fetch(url.toString(), { credentials: 'omit' });
                if (!r.ok) return null;
                return await r.json();
            }""",
            url, params or {}
        )
        return res
    except Exception:
        return None
    finally:
        await page.close()

async def _api_search_by_ean(ctx, ean: str, limit: int) -> List[Dict]:
    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"fq": f"alternateIds_Ean:{ean}", "_from": "0", "_to": str(max(limit*3-1, limit-1))}
    )
    if not isinstance(data, list):
        return []
    items = [_product_to_item(p) for p in data]
    return _dedupe_and_trim(items, limit)

async def _api_search_by_text(ctx, q: str, limit: int) -> List[Dict]:
    items: List[Dict] = []

    # 1) ft=
    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"ft": q, "_from": "0", "_to": str(max(limit*5-1, limit-1))}
    )
    if isinstance(data, list) and data:
        items.extend(_product_to_item(p) for p in data)

    # 2) /search/{q} si ft viene vacío
    if not items:
        data2 = await _api_get(
            ctx,
            f"{BASE}/api/catalog_system/pub/products/search/{q}",
            {"_from": "0", "_to": str(max(limit*5-1, limit-1))}
        )
        if isinstance(data2, list) and data2:
            items.extend(_product_to_item(p) for p in data2)

    return _dedupe_and_trim(items, limit)

# ---------------------------
# Fallback DOM light + __STATE__
# ---------------------------
async def _page_setup_light(page):
    try:
        await page.route("**/*", lambda route: (
            route.abort() if route.request.resource_type in {"image","font","stylesheet","media"}
            else route.continue_()
        ))
    except Exception:
        pass

async def _extract_from_state(page) -> List[Dict]:
    try:
        st_json = await page.evaluate(
            "() => { try { return JSON.stringify(window.__STATE__||null) } catch(e){ return null } }"
        )
        if not st_json:
            return []
        st = json.loads(st_json)
    except Exception:
        return []

    out: List[Dict] = []
    try:
        for _, v in list(st.items()):
            if not isinstance(v, dict):
                continue
            if v.get("__typename") in ("Product","ProductSummary"):
                p = {
                    "productName": v.get("productName") or v.get("productNameWithBrand") or v.get("productTitle"),
                    "linkText": v.get("linkText"),
                    "link": v.get("link"),
                    "items": v.get("items") or [],
                }
                # priceRange → sellingPrice.lowPrice
                pr = (v.get("priceRange") or {}).get("sellingPrice") or {}
                lp = pr.get("lowPrice")
                item = _product_to_item(p)
                if lp and not item.get("price"):
                    item["price"] = _clean_price_to_ars(lp)
                out.append(item)
    except Exception:
        pass
    return out

async def _extract_from_dom_once(page) -> List[Dict]:
    nodes = await page.eval_on_selector_all(
        ".vtex-product-summary-2-x-container, .vtex-search-result-3-x-galleryItem, [class*='product-summary'], [class*='galleryItem']",
        '''(cards) => cards.map(n => {
            const q = s => n.querySelector(s);
            const txt = el => (el?.textContent || '').trim();

            const a = q('a.vtex-product-summary-2-x-clearLink, a.vtex-product-summary-2-x-imageWrapper, a[href*="/p"]');
            const href = a?.getAttribute('href') || '';

            const t = q('h3.vtex-product-summary-2-x-productBrand, span.vtex-product-summary-2-x-productBrand, [data-testid*="product-name"]');
            const title = txt(t);

            const img = q('img');
            const image_url = img?.getAttribute('src') || '';

            const i = q('span[class*="currencyInteger"]')?.textContent || '';
            const g = q('span[class*="currencyGroup"]')?.textContent || '';
            const sep = q('span[class*="currencyDecimal"]')?.textContent || ',';
            const fr = q('span[class*="currencyFraction"]')?.textContent || '00';
            const price = (i || g || fr) ? `ARS ${i}${g}${sep}${fr}` : '';

            return { title, url: href, image_url, price };
        })'''
    )
    out: List[Dict] = []
    for it in nodes or []:
        out.append({
            "title": _normalize_title(it.get("title")),
            "url": _normalize_url(it.get("url")),
            "image_url": it.get("image_url") or None,
            "price": _clean_price_to_ars(it.get("price")),
            "barcode": None,
            "promotion": None,
        })
    return out

async def _dom_fallback(ctx, q: str, limit: int) -> List[Dict]:
    page = await ctx.new_page()
    try:
        await _page_setup_light(page)
        url = f"{BASE}/{q}?_q={q}&map=ft"
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        items = await _extract_from_state(page)
        if len([x for x in items if x.get("title") and x.get("url")]) < limit:
            dom_extra = await _extract_from_dom_once(page)
            items = items + dom_extra
        return _dedupe_and_trim(items, limit)
    finally:
        await page.close()

# ---------------------------
# (Opcional) Enrich PDP con cache + concurrencia limitada
# ---------------------------
_PDP_CACHE: Dict[str, Tuple[float, Dict]] = {}
_PDP_TTL_SEC = 30 * 60
_PDP_SEM = asyncio.Semaphore(2)

async def _pdp_enrich(ctx, url: str, need_price: bool, need_barcode: bool) -> Dict:
    if not url: return {"price": None, "barcode": None}
    now = time.time()
    cached = _PDP_CACHE.get(url)
    if cached and (now - cached[0] <= _PDP_TTL_SEC):
        data = cached[1]
        return {
            "price": data.get("price") if need_price else None,
            "barcode": data.get("barcode") if need_barcode else None,
        }

    async with _PDP_SEM:
        page = await ctx.new_page()
        try:
            await _page_setup_light(page)
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            price = None
            barcode = None
            if need_price:
                price = await page.evaluate("""
                    () => {
                      const q = s => document.querySelector(s);
                      const i = q('span[class*="currencyInteger"]')?.textContent || '';
                      const g = q('span[class*="currencyGroup"]')?.textContent || '';
                      const sep = q('span[class*="currencyDecimal"]')?.textContent || ',';
                      const fr = q('span[class*="currencyFraction"]')?.textContent || '00';
                      const txt = (i || g || fr) ? `ARS ${i}${g}${sep}${fr}` : '';
                      return txt || null;
                    }
                """)
                price = _clean_price_to_ars(price)
            if need_barcode:
                # EAN desde JSON-LD o __STATE__
                # (en Jumbo muchas veces alcanza con referenceId del JSON VTEX; el PDP es "por las dudas")
                try:
                    scripts = await page.query_selector_all('script[type="application/ld+json"]')
                    for s in scripts:
                        raw = (await s.inner_text()).strip()
                        obj = json.loads(raw)
                        seq = obj if isinstance(obj, list) else [obj]
                        for o in seq:
                            if isinstance(o, dict) and (o.get("@type","").lower()=="product"):
                                for k in ("gtin13","gtin14","gtin", "ean"):
                                    d = _only_digits(o.get(k))
                                    if d and 8 <= len(d) <= 14:
                                        barcode = d; break
                        if barcode: break
                except: ...
                if not barcode:
                    st_json = await page.evaluate("() => { try { return JSON.stringify(window.__STATE__||null) } catch(e){ return null } }")
                    if st_json:
                        try:
                            st = json.loads(st_json)
                            txt = json.dumps(st, ensure_ascii=False)
                            for m in DIGITS_ANY.finditer(txt):
                                d = m.group(0)
                                if 8 <= len(d) <= 14:
                                    barcode = d; break
                        except: ...
            _PDP_CACHE[url] = (now, {"price": price, "barcode": barcode})
            return {"price": price if need_price else None, "barcode": barcode if need_barcode else None}
        finally:
            await page.close()

# ---------------------------
# API pública principal
# ---------------------------
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    # 1) EAN → API
    if BARCODE_RE.fullmatch(query):
        items = await _api_search_by_ean(ctx, query, limit)
        return [SearchItem(**it).dict() for it in items]

    # 2) Texto → API (ft / path)
    items = await _api_search_by_text(ctx, query, limit)
    if items:
        return [SearchItem(**it).dict() for it in items]

    # 3) Fallback DOM
    items = await _dom_fallback(ctx, query, limit)

    # (Opcional) Enriquecer solo si falta price/barcode – comentar este bloque si querés puro “fast”
    need_enrich = any(i.get("price") is None or i.get("barcode") is None for i in items)
    if need_enrich:
        tasks, idxs = [], []
        for i, it in enumerate(items):
            need_price = it.get("price") is None
            need_bc = it.get("barcode") is None
            if not need_price and not need_bc:
                continue
            tasks.append(_pdp_enrich(ctx, it["url"], need_price=need_price, need_barcode=need_bc))
            idxs.append(i)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for j, r in enumerate(results):
                if isinstance(r, dict):
                    ii = idxs[j]
                    items[ii]["price"] = items[ii].get("price") or r.get("price")
                    items[ii]["barcode"] = items[ii].get("barcode") or r.get("barcode")

    return [SearchItem(**_dedupe_and_trim(items, limit)[k]).dict()
            for k in range(len(_dedupe_and_trim(items, limit)))]
