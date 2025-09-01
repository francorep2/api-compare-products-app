# main.py
from playwright.sync_api import sync_playwright
import json, urllib.parse, re, time, datetime, os

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"  # host estático con imágenes
COTO_SUCURSAL_ID = 200
TOP_N = 3

# =============== Utils ===============

def clean_text(s):
    if not s: return None
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()

def normalize_price(text):
    if not text:
        return None
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

def save_debug_html(page, tag):
    try:
        os.makedirs("debug", exist_ok=True)
        html = page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html)
        print(f"[LOG] Guardado HTML: {path}")
    except Exception as e:
        print("[LOG] No pude guardar HTML:", e)

def absolutize(base, href, current_url=None):
    if not href: return current_url or base
    if href.startswith("http"): return href
    parts = base.split("/", 3)
    path = href if href.startswith("/") else ("/"+href)
    return f"{parts[0]}//{parts[2]}{path}"

def sanitize_coto_href(href:str) -> str:
    s = urllib.parse.unquote(href)  # decode %3F, %3D, etc
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

# =============== Parsers PDP ===============

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

def parse_meta_price_dom(page):
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
            combo = f"$ {entero},{dec}"
            pr = normalize_price(combo)
            if pr: return pr
        txt = clean_text(cont.inner_text())
        pr = normalize_price(txt)
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

def parse_regex_from_html(page):
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

# -------- Imagen estática Coto --------

STATIC_IMG_REGEX = re.compile(
    r"https?://static\.cotodigital(?:3)?\.com\.ar/sitios/fotos/(?:large|medium|small)/[0-9]{8}/[0-9]{8}\.jpg",
    re.IGNORECASE
)

def sku_from_url(url: str):
    m = re.search(r"/R-([0-9]{8})-", url)
    return m.group(1) if m else None

def folder_from_sku(sku: str):
    # carpeta = SKU con las dos últimas cifras en 00 (agrupado por centenas)
    if not sku or len(sku) != 8: return None
    return sku[:-2] + "00"

def build_static_image_url(sku: str, size="large"):
    folder = folder_from_sku(sku)
    if not folder: return None
    return f"{COTO_STATIC_HOST}/sitios/fotos/{size}/{folder}/{sku}.jpg"

def extract_static_image_from_html(page):
    html = page.content()
    m = STATIC_IMG_REGEX.search(html)
    return m.group(0) if m else None

def extract_image_url(page, final_url):
    # 1) Primero, buscar una imagen estática correcta en el HTML
    url = extract_static_image_from_html(page)
    if url: return url

    # 2) Construirla desde el SKU de la PDP (si está en la URL)
    sku = sku_from_url(final_url)
    if sku:
        built = build_static_image_url(sku, size="large")
        if built: return built

    # 3) JSON-LD / OG / Twitter / link rel / <img> (fallback)
    # JSON-LD
    try:
        jl = parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(COTO_BASE, jl["image"])
    except: pass
    # OG/Twitter
    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if el.count():
            src = el.get_attribute("content")
            if src and "cotodigital" in src:
                return absolutize(COTO_BASE, src)
    # link rel=image_src
    el = page.locator("link[rel='image_src']").first
    if el.count():
        src = el.get_attribute("href")
        if src and "cotodigital" in src:
            return absolutize(COTO_BASE, src)
    # <img> visibles
    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original"]:
                src = el.get_attribute(attr)
                if src and "cotodigital" in src:
                    return absolutize(COTO_BASE, src)
    return None

# =============== PDP Navegación ===============

def fetch_coto_pdp(ctx, href, timeout=60000):
    page = ctx.new_page()
    try:
        url = absolutize(COTO_BASE, href)
        url = sanitize_coto_href(url)
        print(f"[LOG] PDP GET {url}")
        page.goto(url, wait_until="networkidle", timeout=timeout)
        for _ in range(2): page.mouse.wheel(0, 800); time.sleep(0.2)

        # título
        title = None
        for sel in ["h1[itemprop='name']", "h1.product-name", "h1", ".product-name"]:
            el = page.locator(sel).first
            if el.count():
                t = clean_text(el.inner_text())
                if t and len(t)>2: title = t; break

        # precio (JSON-LD -> metas -> DOM -> regex)
        price = None
        jl = parse_jsonld_product(page)
        if jl:
            price = jl.get("price") or price
            if jl.get("title") and not title: title = jl["title"]
        if not price: price = parse_meta_price_dom(page)
        if not price: price = parse_regex_from_html(page)
        if not title: title = clean_text(page.title())

        # imagen estática correcta
        image_url = extract_image_url(page, url)

        return {"title": title, "price": price, "url": url, "image_url": image_url}
    except Exception as e:
        print("[LOG] PDP error:", e)
        save_debug_html(page, "coto_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None}
    finally:
        page.close()

# =============== Search -> PDP ===============

def coto_search_top_n(ctx, query, limit=TOP_N):
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

# =============== Runner ===============

def buscar_coto(query, limit=TOP_N, headless=True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            locale="es-AR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        )
        try:
            return {"coto": coto_search_top_n(ctx, query, limit=limit)}
        finally:
            ctx.close(); browser.close()

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv)>1 else "arroz gallo oro"
    out = buscar_coto(q, limit=3, headless=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
