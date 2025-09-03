# scrapers/carrefour_scraper.py
from typing import List, Dict, Optional
import json, urllib.parse, re, time, datetime, os
from urllib.parse import urlparse

TOP_N = 3
CARREFOUR_BASE = "https://www.carrefour.com.ar"

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

# ===================== Carrefour helpers =====================

def sanitize_carrefour_href(href: str) -> str:
    if not href: return CARREFOUR_BASE
    href = urllib.parse.unquote(href.split("#", 1)[0]).strip()
    return absolutize(CARREFOUR_BASE, href)

def extract_image_carrefour(page) -> Optional[str]:
    try:
        jl = parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(CARREFOUR_BASE, jl["image"])
    except: pass
    for sel in ["meta[property='og:image']","meta[property='og:image:url']","meta[name='twitter:image']","link[rel='image_src']"]:
        el = page.locator(sel).first
        if el.count():
            src = el.get_attribute("content") or el.get_attribute("href")
            if src: return absolutize(CARREFOUR_BASE, src)
    for sel in ["img[itemprop='image']", ".vtex-store-components-3-x-productImageTag", ".product-image img", "img"]:
        el = page.locator(sel).first
        if el.count():
            for attr in ["src","data-src","data-lazy","data-original"]:
                src = el.get_attribute(attr)
                if src: return absolutize(CARREFOUR_BASE, src)
    return None

def _carrefour_accept_cookies(page):
    for bid in ["onetrust-accept-btn-handler", "onetrust-accept-btn-handler-2"]:
        try:
            el = page.locator(f"#{bid}")
            if el.count():
                el.click(timeout=2000); time.sleep(0.2); return
        except: pass
    for txt in ["Aceptar","ACEPTAR","Accept"]:
        try:
            page.get_by_role("button", name=txt).first.click(timeout=1500); time.sleep(0.2); return
        except: pass

def _carrefour_is_pdp_href(href: str) -> bool:
    if not href: return False
    href = href.lower()
    if not href.endswith("/p"): return False
    bad = ["/checkout","/account","/promociones","/ofertas","/landing"]
    return not any(b in href for b in bad)

def _carrefour_collect_anchors(page):
    for sel in [
        "a[href$='/p']",
        "a[href*='/p?']",
        ".vtex-product-summary-2-x-clearLink",
        ".vtex-search-result-3-x-gallery a[href*='/p']",
        "section [data-testid='product-summary'] a[href*='/p']",
    ]:
        try:
            page.wait_for_selector(sel, timeout=5000)
            loc = page.locator(sel)
            if loc and loc.count():
                return loc
        except: pass
    return None

def _carrefour_extract_from_state(page) -> List[str]:
    try:
        state = page.evaluate("() => window.__STATE__ || null")
        if not isinstance(state, dict): return []
        base = CARREFOUR_BASE
        links, seen = [], set()
        for v in state.values():
            if isinstance(v, dict):
                lt = v.get("linkText")
                if lt:
                    href = f"{base}/{lt}/p"
                    if href not in seen and _carrefour_is_pdp_href(href):
                        seen.add(href); links.append(href)
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        lt = it.get("linkText")
                        if lt:
                            href = f"{base}/{lt}/p"
                            if href not in seen and _carrefour_is_pdp_href(href):
                                seen.add(href); links.append(href)
        return links
    except:
        return []

def _carrefour_parse_pdp_from_state(page) -> Dict:
    """Extrae title/price/image desde window.__STATE__ en PDP."""
    try:
        st = page.evaluate("() => window.__STATE__ || null")
        if not isinstance(st, dict): return {}
        title, image_url = None, None
        prices = []

        def _push_price(val):
            try:
                f = float(val)
                if f > 0:
                    prices.append(f)
            except: pass

        for v in st.values():
            if isinstance(v, dict):
                cand_title = v.get("productName") or v.get("name") or v.get("productTitle")
                if not title and isinstance(cand_title, str) and len(cand_title.strip()) > 2:
                    title = clean_text(cand_title)
                pr = v.get("priceRange") or {}
                sp = (pr.get("sellingPrice") or {})
                _push_price(sp.get("highPrice")); _push_price(sp.get("lowPrice"))
                items = v.get("items") or v.get("Item") or []
                if isinstance(items, dict): items = [items]
                for it in items:
                    imgs = it.get("images") or it.get("Images") or []
                    if not image_url and isinstance(imgs, list) and imgs:
                        iu = imgs[0].get("imageUrl") or imgs[0].get("url")
                        if isinstance(iu, str):
                            if iu.startswith("//"): iu = "https:" + iu
                            image_url = absolutize(CARREFOUR_BASE, iu)
                    sellers = it.get("sellers") or it.get("Sellers") or []
                    for s in sellers:
                        co = s.get("commertialOffer") or s.get("CommertialOffer") or {}
                        for key in ["Price","ListPrice","spotPrice","SellingPrice","price"]:
                            if key in co: _push_price(co[key])

        price = None
        if prices:
            mx = max(prices)
            if mx >= 1000:
                price = f"ARS {mx/100:.2f}"
            elif mx >= 100:
                price = f"ARS {mx:.2f}"

        out = {}
        if title: out["title"] = title
        if price: out["price"] = price
        if image_url: out["image_url"] = image_url
        return out
    except:
        return {}

def safe_goto(page, url, wait="domcontentloaded", timeout=20000):
    try:
        return page.goto(url, wait_until=wait, timeout=timeout)
    except Exception as e:
        print(f"[LOG] safe_goto fallback ({wait}) -> {e}")
        try:
            return page.goto(url, timeout=8000)
        except Exception as e2:
            print(f"[LOG] goto simple también falló: {e2}")
            return None

def wait_quick(locator, timeout=2500) -> bool:
    try:
        locator.wait_for(timeout=timeout); return True
    except: return False

# ===================== Carrefour PDP/Search =====================

def fetch_carrefour_pdp(ctx, href, timeout=60000) -> Dict:
    page = ctx.new_page()
    start = time.monotonic()
    MAX_TOTAL = 12.0
    try:
        url = sanitize_carrefour_href(href)
        print(f"[LOG] PDP GET {url}")
        safe_goto(page, url, wait="domcontentloaded", timeout=15000)
        _carrefour_accept_cookies(page)

        for _ in range(3):
            if time.monotonic() - start > MAX_TOTAL: break
            page.mouse.wheel(0, 1000); time.sleep(0.2)

        # Quick title
        title = None
        for sel in ["h1[data-testid='product-name']","h1[itemprop='name']",
                    ".vtex-store-components-3-x-productNameContainer h1","h1"]:
            if wait_quick(page.locator(sel).first, timeout=2000):
                t = clean_text(page.locator(sel).first.inner_text())
                if t and len(t) > 2:
                    title = t; break

        price = None
        jl = parse_jsonld_product(page)
        if jl:
            if jl.get("title") and not title: title = jl["title"]
            try:
                pnum = float((jl.get("price") or "").replace(",", "."))
                if pnum >= 100: price = f"ARS {pnum:.2f}"
            except: pass

        if not price:
            p2 = parse_meta_price_dom(page)
            try:
                if p2:
                    v = float(p2.replace("ARS","").strip())
                    if v >= 100: price = p2
            except: pass

        if not title or not price:
            state_data = _carrefour_parse_pdp_from_state(page)
            if state_data.get("title") and not title: title = state_data["title"]
            if state_data.get("price"): price = state_data["price"]
            image_url = state_data.get("image_url")
        else:
            image_url = extract_image_carrefour(page)

        if not title: title = clean_text(page.title())
        if not image_url: image_url = extract_image_carrefour(page)

        return {"title": title, "price": price, "url": url, "image_url": image_url}
    except Exception as e:
        print("[LOG] PDP error Carrefour:", e)
        save_debug_html(page, "carrefour_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None}
    finally:
        page.close()

def carrefour_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    q = urllib.parse.quote(query)
    urls_try = [
        f"{CARREFOUR_BASE}/{q}?_q={q}&map=ft",
        f"{CARREFOUR_BASE}/buscar?ft={q}",
    ]
    page = ctx.new_page()
    try:
        results, seen = [], set()
        for search_url in urls_try:
            print(f"[LOG] Carrefour search GET {search_url}")
            safe_goto(page, search_url, wait="domcontentloaded", timeout=15000)
            print(f"[LOG] Search title: {page.title()}")

            _carrefour_accept_cookies(page)

            t0 = time.monotonic()
            while time.monotonic() - t0 < 6.0:
                page.mouse.wheel(0, 1000); time.sleep(0.2)

            anchors = _carrefour_collect_anchors(page)
            total = anchors.count() if anchors else 0
            print(f"[LOG] Carrefour anchors visibles: {total}")

            if total:
                for i in range(min(total, limit * 10)):
                    a = anchors.nth(i)
                    href = a.get_attribute("href") or ""
                    if not _carrefour_is_pdp_href(href): continue
                    pdp = fetch_carrefour_pdp(ctx, href)
                    if not pdp["url"] or pdp["url"] in seen: continue
                    seen.add(pdp["url"]); results.append(pdp)
                    if len(results) == limit: return results

            if len(results) < limit:
                state_links = _carrefour_extract_from_state(page)
                print(f"[LOG] Carrefour STATE links: {len(state_links)}")
                for href in state_links:
                    if len(results) == limit: break
                    if href in seen: continue
                    pdp = fetch_carrefour_pdp(ctx, href)
                    if not pdp["url"] or pdp["url"] in seen: continue
                    seen.add(pdp["url"]); results.append(pdp)

            if results:
                return results[:limit]

        if not results:
            save_debug_html(page, "carrefour_search_empty_all")
        return results[:limit]
    except Exception as e:
        print("[LOG] Carrefour search error:", e)
        save_debug_html(page, "carrefour_search_error")
        return []
    finally:
        page.close()

# API pública del scraper
def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    """
    ctx: Browser context provisto por app/services/carrefour.py
    """
    # Nota: el enrutado/abort de trackers lo podés hacer en el servicio (context.route)
    return carrefour_search_top_n(ctx, query, limit=limit)
