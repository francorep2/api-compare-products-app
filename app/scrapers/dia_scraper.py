# scrapers/dia_scraper.py
from typing import List, Dict, Optional
import json, urllib.parse, re, time, datetime, os
from urllib.parse import urljoin

TOP_N = 3
DIA_BASE = "https://diaonline.supermercadosdia.com.ar"

# ===================== Utils =====================

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

# ---- EAN helpers ----
def _digits(s: Optional[str]) -> str: return re.sub(r"\D", "", s or "")
def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s); return d if 8 <= len(d) <= 14 else None
def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

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
                    gtin = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                    return {
                        "title": clean_text(name) if name else None,
                        "price": f"ARS {price:.2f}" if price is not None else None,
                        "image": image or None,
                        "barcode": gtin or None,
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

# === EAN extractors (VTEX) ===

def extract_ean_from_window(page) -> Optional[str]:
    try:
        has = page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if not has: return None
        data = page.evaluate("() => window.skuJson")
        if isinstance(data, dict):
            for s in (data.get("skus") or []):
                bc = choose_barcode(s.get("ean") or s.get("Ean"))
                if bc: return bc
            bc = choose_barcode(data.get("ean") or data.get("Ean"))
            if bc: return bc
    except: pass
    return None

def extract_ean_from_state_or_datalayer(page) -> Optional[str]:
    # dataLayer
    try:
        has_dl = page.evaluate("() => Array.isArray(window.dataLayer)")
        if has_dl:
            arr = page.evaluate("() => window.dataLayer")
            for it in arr:
                if isinstance(it, dict):
                    for k in ("ean","gtin","gtin13","productEAN","productGtin"):
                        bc = choose_barcode(it.get(k))
                        if bc: return bc
    except: pass
    # __STATE__
    try:
        has_state = page.evaluate("() => typeof window.__STATE__ !== 'undefined'")
        if has_state:
            st = page.evaluate("() => window.__STATE__")
            txt = json.dumps(st, ensure_ascii=False)
            m = re.search(r'"ean"\s*:\s*"([^"]+)"', txt)
            if m:
                bc = choose_barcode(m.group(1))
                if bc: return bc
    except: pass
    return None

# ===================== Helpers búsqueda =====================

def _dia_accept_cookies(page):
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

def _dia_is_pdp_href(href: str) -> bool:
    if not href: return False
    href = href.lower()
    return href.endswith("/p") and ("/checkout" not in href and "/account" not in href)

def _dia_collect_anchors(page):
    for sel in [
        "a[href$='/p']",
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

# ===================== PDP & Search =====================

def fetch_dia_pdp(ctx, href, timeout=60000) -> Dict:
    page = ctx.new_page()
    try:
        url = absolutize(DIA_BASE, href)
        print(f"[LOG] DIA PDP GET {url}")
        safe_goto(page, url, wait="domcontentloaded", timeout=15000)
        _dia_accept_cookies(page)

        for _ in range(3):
            page.mouse.wheel(0, 1000); time.sleep(0.2)

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
                if p2 and float(p2.replace("ARS","").strip()) >= 100:
                    price = p2
            except: pass

        image_url = None
        if jl and jl.get("image"): image_url = absolutize(DIA_BASE, jl["image"])
        if not image_url:
            for sel in ["meta[property='og:image']","meta[property='og:image:url']","meta[name='twitter:image']","link[rel='image_src']"]:
                el = page.locator(sel).first
                if el.count():
                    src = el.get_attribute("content") or el.get_attribute("href")
                    if src: image_url = absolutize(DIA_BASE, src); break

        if not title: title = clean_text(page.title())

        # ---- BARCODE ----
        barcode = None
        if jl and jl.get("barcode"): barcode = jl["barcode"]
        if not barcode: barcode = extract_ean_from_window(page)
        if not barcode: barcode = extract_ean_from_state_or_datalayer(page)

        return {"title": title, "price": price, "url": url, "image_url": image_url, "barcode": barcode}
    except Exception as e:
        print("[LOG] DIA PDP error:", e)
        save_debug_html(page, "dia_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None}
    finally:
        page.close()

def dia_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    q = urllib.parse.quote(query)
    urls_try = [
        f"{DIA_BASE}/{q}?_q={q}&map=ft",
        f"{DIA_BASE}/buscar?ft={q}",
    ]
    page = ctx.new_page()
    try:
        results, seen = [], set()
        for search_url in urls_try:
            print(f"[LOG] DIA search GET {search_url}")
            safe_goto(page, search_url, wait="domcontentloaded", timeout=15000)
            _dia_accept_cookies(page)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 6.0:
                page.mouse.wheel(0, 1000); time.sleep(0.2)

            anchors = _dia_collect_anchors(page)
            total = anchors.count() if anchors else 0
            print(f"[LOG] DIA anchors: {total}")

            if total:
                for i in range(min(total, limit * 10)):
                    a = anchors.nth(i)
                    href = a.get_attribute("href") or ""
                    if not _dia_is_pdp_href(href): continue
                    pdp = fetch_dia_pdp(ctx, href)
                    if not pdp["url"] or pdp["url"] in seen: continue
                    seen.add(pdp["url"]); results.append(pdp)
                    if len(results) == limit: return results

        if not results:
            save_debug_html(page, "dia_search_empty")
        return results[:limit]
    except Exception as e:
        print("[LOG] DIA search error:", e)
        save_debug_html(page, "dia_search_error")
        return []
    finally:
        page.close()

# ====== VTEX: búsqueda por EAN ======

def _vtex_search_by_barcode(ctx, ean: str, limit: int = 3) -> List[Dict]:
    api_url = f"{DIA_BASE}/api/catalog_system/pub/products/search/?fq=alternateIds_Ean:{ean}"
    page = ctx.new_page()
    try:
        resp = page.goto(api_url, wait_until="domcontentloaded", timeout=20000)
        if not resp or resp.status != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        out: List[Dict] = []
        for prod in data[:limit]:
            title = (prod.get("productName") or "").strip() or None
            link = prod.get("link") or prod.get("linkText")
            if link:
                if not str(link).startswith("http"):
                    url = urljoin(DIA_BASE + "/", f"{link}/p")
                else:
                    url = link if link.endswith("/p") else (link.rstrip("/") + "/p")
            else:
                url = None
            image_url, bc = None, None
            items = prod.get("items") or []
            if items:
                it0 = items[0]
                bc = (it0.get("ean") or "").strip() or None
                imgs = it0.get("images") or []
                if imgs:
                    image_url = imgs[0].get("imageUrl") or imgs[0].get("imageUrlHttps")
            out.append({
                "title": title, "price": None, "url": url,
                "image_url": image_url, "barcode": bc or ean
            })
        return out
    except Exception:
        return []
    finally:
        page.close()

# ===================== API pública =====================

def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    return dia_search_top_n(ctx, query, limit=limit)

def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean: return _vtex_search_by_barcode(ctx, ean, limit=limit)
    if q and q.strip(): return dia_search_top_n(ctx, q.strip(), limit=limit)
    return []
