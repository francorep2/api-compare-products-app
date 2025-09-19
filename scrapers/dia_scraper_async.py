# scrapers/dia_scraper_async.py
# -*- coding: utf-8 -*-
"""
Scraper DIA (VTEX) – versión optimizada y estable
Estrategia:
  1) Si es EAN → API: /api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}
  2) Si es texto → API: /api/catalog_system/pub/products/search?ft={q}  (fallback: /search/{q})
  3) Solo si ambas fallan → DOM light (1 evaluación) + __STATE__ como extra
  4) Dedupe/normalización y limit al final
  5) Sin $$eval (Playwright Python usa eval_on_selector_all)

Requiere:
  - BrowserContext asíncrono `ctx` (Playwright Python)
  - app.models.search_item.SearchItem
"""

import re
import json
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from app.models.search_item import SearchItem

BASE = "https://diaonline.supermercadosdia.com.ar"

# ---------------------------
# Utils básicos
# ---------------------------
BARCODE_RE = re.compile(r"^\d{8,14}$")
DIGITS_ANY = re.compile(r"\d+")

def _only_digits(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    return d or None

def _normalize_title(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    t = re.sub(r"\s+", " ", t).strip()
    return t or None

def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    try:
        if u.startswith("/"):
            u = f"{BASE}{u}"
        p = urlparse(u)
        scheme = "https"
        netloc = p.netloc.lower()
        path = (p.path or "")
        if path.endswith("/p/"):
            path = path[:-1]
        # limpia trackers
        q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","_ga","sc"}]
        return urlunparse((scheme, netloc, path, "", urlencode(q), ""))
    except Exception:
        return u

def _clean_price_to_ars(val: Optional[str | float | int]) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return f"ARS {float(val):.2f}"
    t = str(val).strip().replace("\xa0", " ").replace("\u00a0", " ")
    t = t.replace("$", "").replace("ARS", "").strip()
    t = t.replace(".", "").replace(",", ".")
    t = re.sub(r"[^\d.]", "", t)
    if not t:
        return None
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
        key = (url, (title or "").lower(), bc or "")
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
# Parseadores de JSON VTEX
# ---------------------------
def _find_barcode_in_product(p: Dict) -> Optional[str]:
    # Busca EAN/GTIN en campos comunes dentro del JSON del producto
    try:
        items = p.get("items") or []
        if items:
            # referenceId
            refids = items[0].get("referenceId") or items[0].get("referenceIds") or []
            for r in refids:
                v = r.get("Value") or r.get("value")
                d = _only_digits(v)
                if d and 8 <= len(d) <= 14:
                    return d
            # ean directo (a veces)
            ean = _only_digits(items[0].get("ean") or items[0].get("EAN"))
            if ean and 8 <= len(ean) <= 14:
                return ean
    except Exception:
        pass
    # búsqueda bruta por si aparece en otro lado
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

    # precio (si viene)
    price = None
    try:
        sellers = (p.get("items") or [])[0].get("sellers") or []
        co = (sellers[0].get("commertialOffer") if sellers else {}) or {}
        if "Price" in co:
            price = _clean_price_to_ars(co["Price"])
    except Exception:
        pass

    # promo (si viene en teasers/clusterHighlights, rara vez)
    promotion = None
    try:
        teasers = (p.get("items") or [])[0].get("sellers", [{}])[0].get("commertialOffer", {}).get("Teasers") or []
        # usamos 'name' si existe
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
# API VTEX
# ---------------------------
async def _api_get(ctx, url: str, params: Optional[Dict] = None) -> Optional[List[Dict]]:
    """Hace GET con ctx.request; si no existe, usa fetch desde una page."""
    if hasattr(ctx, "request") and ctx.request:
        try:
            resp = await ctx.request.get(url, params=params or {}, timeout=25000)
            if not resp.ok:
                return None
            return await resp.json()
        except Exception:
            return None
    # Fallback: fetch desde el browser (sin CORS problemas)
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
    # 1) ft=
    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"ft": q, "_from": "0", "_to": str(max(limit*5-1, limit-1))}
    )
    items: List[Dict] = []
    if isinstance(data, list) and data:
        items.extend(_product_to_item(p) for p in data)

    # 2) /search/{q} si ft vino vacío
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
# Fallback: DOM light + __STATE__
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
                co_price = _clean_price_to_ars(lp) if lp else None
                it = _product_to_item(p)
                it["price"] = it["price"] or co_price
                out.append(it)
    except Exception:
        pass
    return out

async def _extract_from_dom_once(page) -> List[Dict]:
    nodes = await page.eval_on_selector_all(
        ".vtex-product-summary-2-x-container, .vtex-product-summary-2-x-element, [data-testid*='product-summary']",
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
# API pública principal
# ---------------------------
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    # 1) EAN → API
    if BARCODE_RE.fullmatch(query or ""):
        items = await _api_search_by_ean(ctx, query, limit)
        return [SearchItem(**it).dict() for it in items]

    # 2) Texto → API (ft / path)
    items = await _api_search_by_text(ctx, query, limit)
    if items:
        return [SearchItem(**it).dict() for it in items]

    # 3) Fallback DOM (raro, pero por si API devuelve vacío)
    items = await _dom_fallback(ctx, query, limit)
    return [SearchItem(**it).dict() for it in items]
