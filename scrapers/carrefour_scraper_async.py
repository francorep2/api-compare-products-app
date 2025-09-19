# scrapers/carrefour_scraper_async.py
# -*- coding: utf-8 -*-
"""
Scraper CARREFOUR (VTEX) – optimizado con promos robustas
Estrategia:
  1) EAN → API: /api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}
  2) Texto → API: /api/catalog_system/pub/products/search?ft={q} (fallback: /search/{q})
  3) Fallback DOM light + __STATE__ si API no trae resultados
  4) Dedupe/normalización y limit al final
"""

import re
import json
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from playwright.async_api import BrowserContext
from app.models.search_item import SearchItem

BASE = "https://www.carrefour.com.ar"

# ---------------------------
# Utils
# ---------------------------
BARCODE_RE = re.compile(r"^\d{8,14}$")
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
            if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","_ga","sc"}
        ]
        return urlunparse((scheme, netloc, path, "", urlencode(q), ""))
    except Exception:
        return u

def _clean_price_to_ars(val: Optional[str | float | int]) -> Optional[str]:
    if val is None: return None
    if isinstance(val, (int, float)): return f"ARS {float(val):.2f}"
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
# Promos – normalizador corto
# ---------------------------
def _short_promo(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = " ".join(text.split()).lower()
    # normalizaciones básicas
    t = (t.replace("2°", "2da").replace("2ª", "2da").replace("2do", "2da")
           .replace("seg.da", "segunda").replace("seg. da", "segunda").replace("2°.", "2da"))

    # NxM
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo:
        return f"{m_combo.group(1)}x{m_combo.group(2)}"

    # %
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    pct = m_pct.group(1) if m_pct else None

    # 2da unidad al %
    unit_words = r"(unidad(?:es)?|unid\.?|ud\.?|u\.?|und\.?)"
    second_words = r"(2da|segunda)"
    patt_direct = rf"(?:\b{second_words}\b.*\b{unit_words}\b|\b{unit_words}\b.*\b{second_words}\b)"
    patt_llevando2 = r"\bllevando\s*2\b"
    patt_positional = rf"\b(en\s+la\s+{second_words}|en\s+{second_words}|{second_words}\s+al|segunda\s+al|{second_words}\s+unidad)\b"

    has_second_unit = bool(
        re.search(patt_direct, t)
        or re.search(patt_llevando2, t)
        or re.search(patt_positional, t)
        or re.search(r"\b2\s*unidades?\b", t)
    )
    if pct:
        return f"2da al {pct}%" if has_second_unit else f"{pct}% OFF"
    return None

def _collect_promo_strings_from_offer(offer: Dict) -> List[str]:
    out: List[str] = []
    if not isinstance(offer, dict):
        return out

    # Teasers (común en VTEX)
    for t in (offer.get("Teasers") or []):
        for k in ("name", "Name", "title", "Title"):
            v = t.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())

    # Discount highlights
    for arr_key in ("DiscountHighLight", "DiscountHighLights", "discountHighlights", "discountHighLight", "discountHighLights"):
        for d in (offer.get(arr_key) or []):
            if isinstance(d, dict):
                for k in ("Name", "name", "Description", "description", "label", "Label"):
                    v = d.get(k)
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
            elif isinstance(d, str) and d.strip():
                out.append(d.strip())

    # Promotions (algunos VTEX exponen esta lista)
    for p in (offer.get("Promotions") or offer.get("promotions") or []):
        if isinstance(p, dict):
            for k in ("Name", "name", "Description", "description", "label", "Label"):
                v = p.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
        elif isinstance(p, str) and p.strip():
            out.append(p.strip())

    # Cualquier texto “suelto” con % que VTEX deja en commertialOffer
    for k, v in offer.items():
        if isinstance(v, str) and "%" in v:
            out.append(v.strip())

    return out

def _reduce_promotion_from_offer(offer: Dict) -> Optional[str]:
    for s in _collect_promo_strings_from_offer(offer):
        short = _short_promo(s)
        if short:
            return short
    # como fallback, combinamos por si separado no detecta
    joined = " | ".join(_collect_promo_strings_from_offer(offer))
    return _short_promo(joined) if joined else None

# ---------------------------
# Parse JSON VTEX → item
# ---------------------------
def _find_barcode_in_product(p: Dict) -> Optional[str]:
    try:
        items = p.get("items") or []
        if items:
            refids = items[0].get("referenceId") or items[0].get("referenceIds") or []
            for r in refids:
                v = r.get("Value") or r.get("value")
                d = _only_digits(v)
                if d and 8 <= len(d) <= 14:
                    return d
            ean = _only_digits(items[0].get("ean") or items[0].get("EAN"))
            if ean and 8 <= len(ean) <= 14:
                return ean
    except Exception:
        pass
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

    image_url = None
    try:
        items = p.get("items") or []
        imgs = (items[0].get("images") if items else []) or []
        image_url = imgs[0].get("imageUrl") if imgs else None
    except Exception:
        pass

    price = None
    promotion = None
    try:
        items = p.get("items") or []
        sellers = (items[0].get("sellers") or []) if items else []
        co = (sellers[0].get("commertialOffer") if sellers else {}) or {}

        if "Price" in co:
            price = _clean_price_to_ars(co["Price"])

        # <<< AQUÍ: Promoción robusta desde la oferta >>>
        promotion = _reduce_promotion_from_offer(co) or promotion
    except Exception:
        pass

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
async def _api_get(ctx: BrowserContext, url: str, params: Optional[Dict] = None) -> Optional[List[Dict]]:
    if hasattr(ctx, "request") and ctx.request:
        try:
            resp = await ctx.request.get(url, params=params or {}, timeout=25000)
            if not resp.ok:
                return None
            return await resp.json()
        except Exception:
            return None

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

async def _api_search_by_ean(ctx: BrowserContext, ean: str, limit: int) -> List[Dict]:
    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"fq": f"alternateIds_Ean:{ean}", "_from": "0", "_to": str(max(limit*3-1, limit-1))}
    )
    if not isinstance(data, list):
        return []
    items = [_product_to_item(p) for p in data]
    return _dedupe_and_trim(items, limit)

async def _api_search_by_text(ctx: BrowserContext, q: str, limit: int) -> List[Dict]:
    items: List[Dict] = []

    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"ft": q, "_from": "0", "_to": str(max(limit*5-1, limit-1))}
    )
    if isinstance(data, list) and data:
        items.extend(_product_to_item(p) for p in data)

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
                pr = (v.get("priceRange") or {}).get("sellingPrice") or {}
                lp = pr.get("lowPrice")
                it = _product_to_item(p)
                if lp and not it.get("price"):
                    it["price"] = _clean_price_to_ars(lp)
                out.append(it)
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

            const promos = Array.from(n.querySelectorAll(
              ".vtex-product-summary-2-x-discountBadgeText, \
               .vtex-product-highlights-2-x-productHighlightText, \
               .vtex-product-highlights-2-x-productHighlightText--promotions, \
               .discount, .badge, .promo, .teaser, \
               [class*='highlight'], [data-testid*='promo']"
            )).map(e => txt(e)).filter(Boolean);

            return { title, url: href, image_url, price, promos };
        })'''
    )
    out: List[Dict] = []
    for it in nodes or []:
        promo_short = None
        for frag in (it.get("promos") or []):
            promo_short = _short_promo(frag)
            if promo_short:
                break
        out.append({
            "title": _normalize_title(it.get("title")),
            "url": _normalize_url(it.get("url")),
            "image_url": it.get("image_url") or None,
            "price": _clean_price_to_ars(it.get("price")),
            "barcode": None,
            "promotion": promo_short,
        })
    return out

async def _dom_fallback(ctx: BrowserContext, q: str, limit: int) -> List[Dict]:
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
async def search(ctx: BrowserContext, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
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
    return [SearchItem(**it).dict() for it in items]
