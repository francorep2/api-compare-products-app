# scrapers/coto_scraper.py
from typing import List, Dict, Optional
import json, urllib.parse, re, time, datetime, os

TOP_N = 3

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

# ===================== Utils comunes =====================

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()

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

def save_debug_html(page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        html = page.content()
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

# ===================== Parsers comunes =====================

def parse_jsonld_product(page):
    scripts = page.locator("script[type='application/ld+json']")
    for i in range(scripts.count()):
        try:
            raw = scripts.nth(i).inner_text().strip()
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict) and ("Product" in str(it.get("@type"))):
                    name = it.get("name")
                    offers = it.get("offers")
                    price = None
                    if isinstance(offers, list) and offers: offers = offers[0]
                    if isinstance(offers, dict):
                        p = offers.get("price")
                        if p:
                            try: price = float(str(p).replace(",", "."))
                            except: price = None
                    image = it.get("image")
                    if isinstance(image, list) and image:
                        image = image[0]
                    return {
                        "title": clean_text(name) if name else None,
                        "price": f"ARS {price:.2f}" if price is not None else None,
                        "image": image or None
                    }
        except: pass
    return None

def parse_meta_price_dom(page) -> Optional[str]:
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        el = page.locator(sel).first
        if el.count():
            val = el.get_attribute("content")
            if val:
                try: return f"ARS {float(str(val).replace(',','.')):.2f}"
                except: pass
    for cont_sel in ["#skuBestPrice", ".skuBestPrice", ".best-price", ".bestPrice", ".price", ".new-price"]:
        cont = page.locator(cont_sel).first
        if not cont.count(): continue
        val_el = cont.locator(".val").first
        dec_el = cont.locator(".dec").first
        if val_el.count():
            entero = (val_el.inner_text() or "").strip()
            dec = (dec_el.inner_text() or "").strip() if dec_el.count() else "00"
            pr = normalize_price(f"$ {entero},{dec}")
            if pr: return pr
        pr = normalize_price(clean_text(cont.inner_text()))
        if pr: return pr
    for sel in ["span:has-text('$')", "div:has-text('$')"]:
        el = page.locator(sel).first
        if el.count():
            pr = normalize_price(clean_text(el.inner_text()))
            if pr: return pr
    try:
        has = page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if has:
            data = page.evaluate("() => window.skuJson")
            if isinstance(data, dict):
                for s in (data.get("skus") or []):
                    bp = s.get("bestPrice")
                    if bp: return f"ARS {float(bp)/100:.2f}"
    except: pass
    return None

def parse_regex_from_html(page) -> Optional[str]:
    html = page.content()
    m = re.findall(r"(?:\$|ARS)\s*[\d\. ]{1,}(?:,\d{2})?", html, flags=re.I)
    best = None
    for s in m:
        pr = normalize_price(s)
        if pr:
            val = float(pr.replace("ARS","").replace("$","").strip())
            if not best or val > best[0]:
                best = (val, pr)
    return best[1] if best else None

# ===================== Imagen estática Coto =====================

STATIC_IMG_REGEX = re.compile(
    r"https?://static\.cotodigital(?:3)?\.com\.ar/sitios/fotos/(?:large|medium|small)/[0-9]{8}/[0-9]{8}\.jpg",
    re.IGNORECASE
)

def sku_from_url(url: str) -> Optional[str]:
    m = re.search(r"/R-([0-9]{8})-", url)
    return m.group(1) if m else None

def folder_from_sku(sku: str) -> Optional[str]:
    if not sku or len(sku) != 8: return None
    return sku[:-2] + "00"

def build_static_image_url(sku: str, size="large") -> Optional[str]:
    folder = folder_from_sku(sku)
    if not folder: return None
    return f"{COTO_STATIC_HOST}/sitios/fotos/{size}/{folder}/{sku}.jpg"

def extract_static_image_from_html(page) -> Optional[str]:
    html = page.content()
    m = STATIC_IMG_REGEX.search(html)
    return m.group(0) if m else None

def extract_image_url(page, final_url) -> Optional[str]:
    url = extract_static_image_from_html(page)
    if url: return url
    sku = sku_from_url(final_url)
    if sku:
        built = build_static_image_url(sku, size="large")
        if built: return built
    try:
        jl = parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(COTO_BASE, jl["image"])
    except: pass
    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if el.count():
            src = el.get_attribute("content")
            if src and "cotodigital" in src:
                return absolutize(COTO_BASE, src)
    el = page.locator("link[rel='image_src']").first
    if el.count():
        src = el.get_attribute("href")
        if src and "cotodigital" in src:
            return absolutize(COTO_BASE, src)
    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original"]:
                src = el.get_attribute(attr)
                if src and "cotodigital" in src:
                    return absolutize(COTO_BASE, src)
    return None

# ===================== Coto PDP/Search =====================

def sanitize_coto_href(href:str) -> str:
    s = urllib.parse.unquote(href)
    while "&&" in s: s = s.replace("&&", "&")
    s = s.replace("?&", "?")
    if "?" in s:
        base, query = s.split("?", 1)
    else:
        base, query = s, ""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)
    if "Dy" not in q: q["Dy"] = ["1"]
    if "idSucursal" not in q: q["idSucursal"] = [str(COTO_SUCURSAL_ID)]
    pairs = []
    for k in ["Dy", "idSucursal"]:
        if k in q:
            for v in q[k]: pairs.append((k,v))
            del q[k]
    for k,vs in q.items():
        for v in vs: pairs.append((k,v))
    new_query = urllib.parse.urlencode(pairs, doseq=True)
    return f"{base}?{new_query}"

def fetch_coto_pdp(ctx, href, timeout=60000) -> Dict:
    page = ctx.new_page()
    try:
        url = absolutize(COTO_BASE, href)
        url = sanitize_coto_href(url)
        print(f"[LOG] PDP GET {url}")
        page.goto(url, wait_until="networkidle", timeout=timeout)
        for _ in range(2): page.mouse.wheel(0, 800); time.sleep(0.2)

        title = None
        for sel in ["h1[itemprop='name']", "h1.product-name", "h1", ".product-name"]:
            el = page.locator(sel).first
            if el.count():
                t = clean_text(el.inner_text())
                if t and len(t)>2: title = t; break

        price = None
        jl = parse_jsonld_product(page)
        if jl:
            price = jl.get("price") or price
            if jl.get("title") and not title: title = jl["title"]
        if not price: price = parse_meta_price_dom(page)
        if not price: price = parse_regex_from_html(page)
        if not title: title = clean_text(page.title())

        image_url = extract_image_url(page, url)

        return {"title": title, "price": price, "url": url, "image_url": image_url}
    except Exception as e:
        print("[LOG] PDP error Coto:", e)
        save_debug_html(page, "coto_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None}
    finally:
        page.close()

def coto_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    search_url = f"{COTO_BASE}/sitios/cdigi/categoria?_dyncharset=utf-8&Dy=1&Ntt={urllib.parse.quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    page = ctx.new_page()
    try:
        print(f"[LOG] Coto classic GET {search_url}")
        resp = page.goto(search_url, wait_until="domcontentloaded", timeout=50000)
        print(f"[LOG] Classic status: {resp.status if resp else 'N/A'} | title: {page.title()}")

        page.wait_for_selector("a[href*='/sitios/cdigi/productos/']", timeout=9000)
        anchors = page.locator("a[href*='/sitios/cdigi/productos/']")
        total = anchors.count()
        print(f"[LOG] Anchors producto: {total}")

        results, seen = [], set()
        for i in range(min(total, limit*6)):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            if not href: continue
            pdp = fetch_coto_pdp(ctx, href)
            if not pdp["url"] or pdp["url"] in seen: continue
            seen.add(pdp["url"])
            results.append(pdp)
            if len(results) == limit: break

        if not results: save_debug_html(page, "coto_classic_empty")
        return results[:limit]
    except Exception as e:
        print("[LOG] Classic search error:", e)
        save_debug_html(page, "coto_classic_error")
        return []
    finally:
        page.close()

# API pública del scraper
def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    return coto_search_top_n(ctx, query, limit=limit)
