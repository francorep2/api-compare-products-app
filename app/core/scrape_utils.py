# app/core/scrape_utils.py
import re, json

def choose_barcode(*cands):
    for c in cands:
        if not c:
            continue
        s = re.sub(r"\D", "", str(c))
        if 8 <= len(s) <= 14:
            return s
    return None

def parse_jsonld_for_gtin_html(html: str) -> str | None:
    try:
        for raw in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, flags=re.S|re.I):
            try:
                data = json.loads(raw)
            except:
                continue
            stack = [data]
            while stack:
                it = stack.pop()
                if isinstance(it, dict):
                    t = it.get("@type")
                    if ("Product" in (t if isinstance(t, str) else str(t))):
                        bc = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                        if bc: return bc
                    stack.extend(it.values())
                elif isinstance(it, list):
                    stack.extend(it)
    except:
        pass
    return None

def extract_ean_from_window(page):
    # VTEX: window.skuJson.skus[].ean
    try:
        has = page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if has:
            data = page.evaluate("() => window.skuJson")
            if isinstance(data, dict):
                for s in (data.get("skus") or []):
                    bc = choose_barcode(s.get("ean") or s.get("Ean"))
                    if bc: return bc
                bc = choose_barcode(data.get("ean") or data.get("Ean"))
                if bc: return bc
    except: pass
    return None

def extract_ean_from_state_or_datalayer(page):
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
