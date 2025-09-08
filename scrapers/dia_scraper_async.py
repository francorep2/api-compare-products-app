# scrapers_async/dia_scraper_async.py
from typing import List, Dict, Optional
import json, urllib.parse, re, os, datetime, asyncio
import re

def _query_terms(q: str) -> list[str]:
    if not q: return []
    return [t for t in re.findall(r"[A-Za-z0-9]{3,}", q.lower())]

def _title_matches(q_terms: list[str], title: str | None) -> bool:
    if not title or not q_terms:
        return True
    tl = title.lower()
    return any(t in tl for t in q_terms)

async def _try_close_banners(page):
    # Cierra banners de cookies/geo (OneTrust, etc.)
    selectors = [
        "[id*=onetrust-accept]", "#onetrust-accept-btn-handler",
        "button:has-text('Aceptar')", "button:has-text('Entendido')",
        "button:has-text('Continuar')", "[data-testid*=accept]"
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=1000)
                await asyncio.sleep(0.2)
        except: pass

async def _vtex_api_search(page, base_url: str, query: str, limit: int):
    """Usa VTEX Catalog Search API: /api/catalog_system/pub/products/search/<query>"""
    api = f"{base_url}/api/catalog_system/pub/products/search/{query}"
    try:
        resp = await page.request.get(api, timeout=30000)
        if resp.ok:
            data = await resp.json()
            items = []
            for p in data[: max(limit*3, limit)]:  # traemos más y luego filtramos
                title = p.get("productName") or p.get("productTitle")
                link_text = p.get("linkText") or p.get("link") or ""
                url = f"{base_url}/{link_text}/p" if link_text else p.get("link") or ""
                # precio (primer seller/item que tenga Price)
                price = None
                image_url = None
                barcode = None
                for it in (p.get("items") or []):
                    # ean/ref
                    if not barcode:
                        # 1) value en referenceId
                        for rid in (it.get("referenceId") or []):
                            val = rid.get("Value")
                            if val and 8 <= len(re.sub(r"\D","",val)) <= 14:
                                barcode = re.sub(r"\D","",val); break
                        # 2) ean directo
                        ean = it.get("ean")
                        if not barcode and ean:
                            d = re.sub(r"\D","", str(ean))
                            if 8 <= len(d) <= 14: barcode = d
                    # imagen
                    if not image_url:
                        imgs = it.get("images") or []
                        if imgs:
                            image_url = imgs[0].get("imageUrl")
                    # precio
                    for seller in (it.get("sellers") or []):
                        co = (seller.get("commertialOffer") or {})
                        pr = co.get("Price")
                        if isinstance(pr, (float, int)) and pr > 0:
                            price = f"ARS {pr:.2f}"
                            break
                    if price and image_url:
                        break
                items.append({
                    "title": title, "price": price, "url": url,
                    "image_url": image_url, "barcode": barcode
                })
            return items
    except Exception as e:
        print("[LOG] VTEX API error:", e)
    return []

TOP_N = 3

# Sitio Día Argentina (VTEX)
DIA_BASE = "https://diaonline.supermercadosdia.com.ar"

def _search_url(query: str) -> str:
    # VTEX: /busca?ft=<query>
    return f"{DIA_BASE}/busca?ft={urllib.parse.quote(query)}"

# ============== Utils ==============

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()

def _digits(s: Optional[str]) -> str:
    import re as _re
    return _re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if 8 <= len(d) <= 14 else None

def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    import re as _re
    t = text.replace("\xa0", " ").strip()
    cand = []
    for ent, dec in _re.findall(r'(?:\$|ARS)\s*([0-9]{1,3}(?:[.\s][0-9]{3})+|[0-9]{4,})(?:,([0-9]{2}))?', t, flags=_re.I):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{ent_clean}.{dec or '00'}"))
        except: pass
    for ent, dec in _re.findall(r'(?:\$|ARS)\s*([0-9]{1,4})\s+([0-9]{2})', t, flags=_re.I):
        try: cand.append(float(f"{int(ent)}.{dec}"))
        except: pass
    for ent, dec in _re.findall(r'([0-9]{1,3}(?:[.\s][0-9]{3})*|[0-9]{1,4}),([0-9]{2})', t):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{int(ent_clean)}.{dec}"))
        except: pass
    for ent in _re.findall(r'\b([0-9]{4,})\b', t):
        try: cand.append(float(int(ent)))
        except: pass
    if not cand: return None
    return f"ARS {max(cand):.2f}"

async def save_debug_html(page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        html = await page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html)
        print(f"[LOG] Guardado HTML: {path}")
    except Exception as e:
        print("[LOG] No pude guardar HTML:", e)

def absolutize(base: str, href: str, current_url: Optional[str] = None) -> str:
    if not href: return current_url or base
    if href.startswith("http"): return href
    parts = base.split("/", 3)
    path = href if href.startswith("/") else ("/"+href)
    return f"{parts[0]}//{parts[2]}{path}"

# ============== Parsers ==============

async def parse_jsonld_product(page):
    scripts = page.locator("script[type='application/ld+json']")
    count = await scripts.count()
    for i in range(count):
        try:
            raw = (await scripts.nth(i).inner_text()).strip()
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict) and ("Product" in str(it.get("@type"))):
                    name = it.get("name")
                    offers = it.get("offers")
                    price = None
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        p = offers.get("price")
                        if p:
                            try: price = float(str(p).replace(",", "."))
                            except: price = None
                    image = it.get("image")
                    if isinstance(image, list) and image:
                        image = image[0]
                    gtin = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                    return {
                        "title": clean_text(name) if name else None,
                        "price": f"ARS {price:.2f}" if price is not None else None,
                        "image": image or None,
                        "barcode": gtin or None,
                    }
        except: pass
    return None

async def parse_meta_price_dom(page) -> Optional[str]:
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        el = page.locator(sel).first
        if await el.count():
            val = await el.get_attribute("content")
            if val:
                try: return f"ARS {float(str(val).replace(',','.')):.2f}"
                except: pass
    for cont_sel in ["#skuBestPrice", ".skuBestPrice", ".best-price", ".bestPrice", ".price", ".new-price"]:
        cont = page.locator(cont_sel).first
        if not await cont.count():
            continue
        val_el = cont.locator(".val").first
        dec_el = cont.locator(".dec").first
        if await val_el.count():
            entero = (await val_el.inner_text() or "").strip()
            dec = (await dec_el.inner_text() or "").strip() if await dec_el.count() else "00"
            pr = normalize_price(f"$ {entero},{dec}")
            if pr: return pr
        pr = normalize_price(clean_text(await cont.inner_text()))
        if pr: return pr
    for sel in ["span:has-text('$')", "div:has-text('$')"]:
        el = page.locator(sel).first
        if await el.count():
            pr = normalize_price(clean_text(await el.inner_text()))
            if pr: return pr
    try:
        has = await page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if has:
            data = await page.evaluate("() => window.skuJson")
            if isinstance(data, dict):
                for s in (data.get("skus") or []):
                    bp = s.get("bestPrice")
                    if bp: return f"ARS {float(bp)/100:.2f}"
    except: pass
    return None

async def parse_regex_from_html(page) -> Optional[str]:
    html = await page.content()
    m = re.findall(r"(?:\$|ARS)\s*[\d\. ]{1,}(?:,\d{2})?", html, flags=re.I)
    best = None
    for s in m:
        pr = normalize_price(s)
        if pr:
            val = float(pr.replace("ARS","").replace("$","").strip())
            if not best or val > best[0]:
                best = (val, pr)
    return best[1] if best else None

async def extract_image_url(page) -> Optional[str]:
    try:
        jl = await parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(DIA_BASE, jl["image"])
    except: pass
    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if await el.count():
            src = await el.get_attribute("content")
            if src:
                return absolutize(DIA_BASE, src)
    el = page.locator("link[rel='image_src']").first
    if await el.count():
        src = await el.get_attribute("href")
        if src:
            return absolutize(DIA_BASE, src)
    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if await el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original"]:
                src = await el.get_attribute(attr)
                if src:
                    return absolutize(DIA_BASE, src)
    return None

async def extract_barcode(page) -> Optional[str]:
    # dataLayer
    try:
        has_dl = await page.evaluate("() => Array.isArray(window.dataLayer)")
        if has_dl:
            arr = await page.evaluate("() => window.dataLayer")
            for it in arr:
                if isinstance(it, dict):
                    for k in ("ean","gtin","gtin13","productEAN","productGtin","codigoBarras","barcode"):
                        val = it.get(k)
                        d = normalize_barcode(val if val is not None else None)
                        if d: return d
    except: pass
    # JSON-LD crudo
    try:
        html = await page.content()
        for raw in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, flags=re.S|re.I):
            try: data = json.loads(raw)
            except: continue
            stack = [data]
            while stack:
                it = stack.pop()
                if isinstance(it, dict):
                    t = it.get("@type")
                    if "Product" in (t if isinstance(t, str) else str(t)):
                        bc = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                        if bc: return bc
                    stack.extend(it.values())
                elif isinstance(it, list):
                    stack.extend(it)
    except: pass
    # Regex
    try:
        txt = await page.content()
        nums = re.findall(r'\b(\d{8,14})\b', txt)
        for pref in (14, 13, 12, 8):
            for n in nums:
                if len(n) == pref:
                    d = normalize_barcode(n)
                    if d: return d
    except: pass
    return None

# ============== PDP & Search ==============

async def fetch_pdp(ctx, href: str, timeout=60000) -> Dict:
    page = await ctx.new_page()
    try:
        url = absolutize(DIA_BASE, href)
        print(f"[LOG] Día PDP GET {url}")
        await page.goto(url, wait_until="networkidle", timeout=timeout)
        for _ in range(2):
            await page.mouse.wheel(0, 800)
            await asyncio.sleep(0.2)

        # título
        title = None
        for sel in ["h1[itemprop='name']", "h1.productName", "h1.product-name", "h1", ".product-name"]:
            el = page.locator(sel).first
            if await el.count():
                t = clean_text(await el.inner_text())
                if t and len(t) > 2:
                    title = t
                    break

        # precio
        price = None
        jl = await parse_jsonld_product(page)
        if jl:
            price = jl.get("price") or price
            if jl.get("title") and not title:
                title = jl["title"]
        if not price: price = await parse_meta_price_dom(page)
        if not price: price = await parse_regex_from_html(page)
        if not title: title = clean_text(await page.title())

        image_url = await extract_image_url(page)
        barcode = jl.get("barcode") if jl and jl.get("barcode") else None
        if not barcode:
            barcode = await extract_barcode(page)

        return {"title": title, "price": price, "url": url, "image_url": image_url, "barcode": barcode}
    except Exception as e:
        print("[LOG] Día PDP error:", e)
        await save_debug_html(page, "dia_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None}
    finally:
        await page.close()

async def dia_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    base = "https://diaonline.supermercadosdia.com.ar"
    q_terms = _query_terms(query)
    page = await ctx.new_page()
    try:
        # 1) API VTEX
        api_items = await _vtex_api_search(page, base, query, limit)
        api_items = [it for it in api_items if _title_matches(q_terms, it.get("title"))]
        if api_items:
            return api_items[:limit]

        # 2) Fallback DOM
        url = f"{base}/busca?ft={urllib.parse.quote(query)}"
        print(f"[LOG] Día search GET {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=50000)
        await _try_close_banners(page)

        try:
            await page.wait_for_selector("a[href*='/p']", timeout=9000)
        except:
            await save_debug_html(page, "dia_search_empty_grid")
            return []

        anchors = page.locator("a[href*='/p']:not([href*='promociones']):not([href^='#'])")
        total = await anchors.count()
        results, seen = [], set()
        for i in range(min(total, limit * 8)):
            href = (await anchors.nth(i).get_attribute("href")) or ""
            if not href or "/promociones" in href or href.startswith("#"):
                continue
            pdp = await fetch_pdp(ctx, href)
            if not pdp.get("url") or pdp["url"] in seen:
                continue
            if not _title_matches(q_terms, pdp.get("title")):
                continue
            seen.add(pdp["url"]); results.append(pdp)
            if len(results) >= limit: break

        if not results:
            await save_debug_html(page, "dia_search_no_matches")
        return results[:limit]
    except Exception as e:
        print(f"[LOG] Día search error: {e}")
        await save_debug_html(page, "dia_search_error")
        return []
    finally:
        await page.close()



async def dia_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    """
    Día (VTEX) no siempre expone EAN en PDP:
    - Buscar por texto con el EAN
    - Filtrar si encontramos el barcode; sino devolver top-N
    """
    prelim = await dia_search_top_n(ctx, ean, limit=limit * 4)
    out = []
    for it in prelim:
        bc = it.get("barcode")
        if bc and bc == ean:
            out.append(it)
        if len(out) == limit:
            break
    return out or prelim[:limit]

# API pública
async def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    return await dia_search_top_n(ctx, query, limit=limit)

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        return await dia_search_by_barcode(ctx, ean, limit=limit)
    if q and q.strip():
        return await dia_search_top_n(ctx, q.strip(), limit=limit)
    return []
