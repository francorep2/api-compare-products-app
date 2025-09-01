# main.py
from playwright.sync_api import sync_playwright
import json, urllib.parse, re, time, os, datetime

# =========================
# Utilidades
# =========================

def clean_text(s):
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def normalize_price(text):
    if not text: return None
    t = text.replace("\xa0", " ")
    # $ 1.234,56  |  1.234,56  |  1234,56  |  1234
    m = re.search(r"\$?\s*([\d\.]+(?:,\d{2})?|\d+)", t)
    if not m: return None
    raw = m.group(1)
    raw = raw.replace(".", "").replace(",", ".")
    try:
        val = float(raw)
        return f"ARS {val:.2f}"
    except:
        return None

def absolutize(base, href, current_url=None):
    if not href:
        return current_url or base
    if href.startswith("http"): return href
    parts = base.split("/", 3)
    return f"{parts[0]}//{parts[2]}{href if href.startswith('/') else '/' + href}"

def save_debug_html(page, tag):
    try:
        html = page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"debug_{tag}_{ts}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[LOG] Guardado HTML para depurar: {path}")
    except Exception as e:
        print("[LOG] No pude guardar HTML:", e)

# =========================
# VTEX (Carrefour/Coto) por API
# =========================

def vtex_api_products(request, base, query, limit=3, try_sc=True, extra_bases=None):
    q = urllib.parse.quote(query)
    bases = [base] + (extra_bases or [])
    endpoints = [
        f"/api/catalog_system/pub/products/search/?ft={q}",
        f"/api/catalog_system/pub/products/search/{q}",
    ]
    if try_sc:
        endpoints += [
            f"/api/catalog_system/pub/products/search/?ft={q}&sc=1",
            f"/api/catalog_system/pub/products/search/?ft={q}&sc=2",
        ]
    headers = {
        "Accept": "application/json",
        "Accept-Language": "es-AR,es;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    for b in bases:
        for ep in endpoints:
            url = f"{b}{ep}"
            resp = request.get(url, headers=headers, timeout=35000)
            print(f"[LOG] VTEX GET {url} -> {resp.status}")
            if not resp.ok:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            if not isinstance(data, list) or len(data) == 0:
                continue
            results = []
            for prod in data[:limit]:
                name = (prod.get("productName")
                        or prod.get("productTitle")
                        or prod.get("productNameComplete"))
                link_text = prod.get("linkText") or prod.get("link")
                price = None
                for it in prod.get("items", []):
                    for se in it.get("sellers", []):
                        offer = se.get("commertialOffer") or {}
                        p = offer.get("Price") or offer.get("spotPrice") or offer.get("ListPrice")
                        if p and float(p) > 0:
                            price = float(p); break
                    if price: break
                if name and link_text:
                    results.append({
                        "title": clean_text(name),
                        "price": f"ARS {price:.2f}" if price is not None else None,
                        "url": f"{b}/{link_text}/p"
                    })
            if results:
                return results
    return []

# =========================
# Fallback DOM VTEX (Carrefour)
# =========================

def scrape_dom_products_vtex(page, base, limit=3):
    selectors_cards = [
        "[data-testid='product-summary-container']",
        "[class*='vtex-product-summary-2-x-container']",
        ".vtex-search-result-3-x-galleryItem",
        "article[class*='product'], li[class*='product']",
    ]
    sel_name = "[data-testid='product-name'], [class*='productBrand'], h2 a, h3 a, a[title]"
    sel_price = "[data-testid*='price'], [class*='vtex-product-price-1-x-sellingPriceValue'], [class*='price'], span:has-text('$')"
    products = []
    for csel in selectors_cards:
        cards = page.locator(csel)
        if cards.count() == 0:
            continue
        for i in range(min(limit, cards.count())):
            card = cards.nth(i)
            title = clean_text(card.locator(sel_name).first.inner_text()) if card.locator(sel_name).count() else None
            price_text = clean_text(card.locator(sel_price).first.inner_text()) if card.locator(sel_price).count() else None
            price = normalize_price(price_text) or price_text
            href = None
            if card.locator("a").count():
                href = card.locator("a").first.get_attribute("href")
            url = absolutize(base, href, page.url)
            products.append({"title": title, "price": price, "url": url})
        if products:
            break
    return products

# =========================
# Coto clásico: selectores + escaneo JS
# =========================

def coto_classic_search(page, query, sucursal_id=200, limit=3):
    base = "https://www.cotodigital.com.ar"
    q = urllib.parse.quote(query)
    url = f"{base}/sitios/cdigi/categoria?_dyncharset=utf-8&Dy=1&Ntt={q}&idSucursal={sucursal_id}"
    print(f"[LOG] Coto classic GET {url}")
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    print(f"[LOG] Classic status: {resp.status if resp else 'N/A'} | title: {page.title()}")

    # peinar un poco la página
    for _ in range(3):
        page.mouse.wheel(0, 1400); time.sleep(0.35)

    # 1) Selectores posibles (vista clásica suele variar)
    card_selectors = [
        ".product", ".product-item", ".item", "li[product-id]", "article.product", ".product-list .item",
        ".product__item", "div[class*='product']:has(a)", "li:has(a)"
    ]
    name_selectors = [
        ".product-name a", ".product-name", "h3 a", "h3", "a[title]", "a"
    ]
    price_selectors = [
        ".best-price", ".price", ".new-price", "span:has-text('$')", "div:has-text('$')"
    ]

    found = []
    for csel in card_selectors:
        cards = page.locator(csel)
        cnt = cards.count()
        if cnt == 0: continue
        for i in range(min(limit*3, cnt)):  # miramos un poco más de lo usual
            card = cards.nth(i)
            # nombre
            title = None
            for ns in name_selectors:
                loc = card.locator(ns).first
                if loc.count():
                    t = clean_text(loc.inner_text())
                    if t and len(t) > 2:
                        title = t; break
            # precio
            price_val = None
            for ps in price_selectors:
                loc = card.locator(ps).first
                if loc.count():
                    price_val = normalize_price(loc.inner_text()) or clean_text(loc.inner_text())
                    if price_val: break
            # link
            href = None
            if card.locator("a").count():
                href = card.locator("a").first.get_attribute("href")
            urlp = absolutize(base, href, current_url=page.url)
            if title or price_val or urlp:
                found.append({"title": title, "price": price_val, "url": urlp})
            if len(found) >= limit:
                break
        if found:
            break

    # 2) Si no encontramos suficiente con selectores, escaneo JS general
    if len(found) < limit:
        js = """
        () => {
          const anchors = Array.from(document.querySelectorAll('a')).filter(a => {
            const h = (a.getAttribute('href')||'').toLowerCase();
            const txt = (a.textContent||'').trim();
            // heurística: links que parecen de producto
            return h.includes('/producto') || h.includes('/prod') || h.includes('/pdp') || h.includes('/product') || (txt.length > 4 && !h.includes('#') && !h.includes('javascript'));
          });

          const seen = new Set();
          const items = [];
          function getPriceAround(el) {
            let node = el;
            for (let depth=0; depth<5 && node; depth++) {
              const priceNode = node.querySelector("span,div,em,strong");
              if (priceNode && /\\$/i.test(priceNode.textContent)) {
                return priceNode.textContent.trim();
              }
              node = node.parentElement;
            }
            return null;
          }

          for (const a of anchors) {
            const href = a.getAttribute('href') || '';
            const title = (a.getAttribute('title') || a.textContent || '').trim();
            if (!title || title.length < 3) continue;
            const key = href + '|' + title;
            if (seen.has(key)) continue;
            seen.add(key);

            const priceText = getPriceAround(a);
            items.push({ href, title, priceText });
            if (items.length > 30) break;
          }
          return items;
        }
        """
        items = page.evaluate(js)
        base = "https://www.cotodigital.com.ar"
        for it in items:
            urlp = absolutize(base, it.get("href") or "", page.url)
            price = normalize_price(it.get("priceText")) or clean_text(it.get("priceText"))
            title = clean_text(it.get("title"))
            # filtrar ruido de navegación/categorías
            if title and len(title) < 4: 
                continue
            found.append({"title": title, "price": price, "url": urlp})
            if len(found) >= limit:
                break

    # 3) Deduplicar y recortar
    dedup = []
    seen = set()
    for p in found:
        key = (p.get("title"), p.get("url"))
        if key in seen: continue
        seen.add(key)
        dedup.append(p)
        if len(dedup) == limit: break

    # 4) Si quedó vacío, guardamos HTML para ver estructura real
    if not dedup:
        save_debug_html(page, "coto_classic")

    return dedup

# =========================
# Búsqueda principal
# =========================

def buscar_producto(query, limit=3, headless=True, COTO_SUCURSAL_ID=200):
    carrefour_base = "https://www.carrefour.com.ar"
    coto_base = "https://www.cotodigital.com.ar"
    coto_alt  = "https://www.cotodigital3.com.ar"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            locale="es-AR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        )
        page = ctx.new_page()
        out = {"carrefour": [], "coto": []}

        # ----- Carrefour
        try:
            prods = vtex_api_products(ctx.request, carrefour_base, query, limit=limit)
            if not prods:
                search_url = f"{carrefour_base}/{urllib.parse.quote(query)}?map=ft&_q={urllib.parse.quote(query)}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=32000)
                for _ in range(2): page.mouse.wheel(0, 1400); time.sleep(0.3)
                prods = scrape_dom_products_vtex(page, carrefour_base, limit)
            out["carrefour"] = prods
        except Exception as e:
            out["carrefour"] = [{"error": str(e)}]

        # ----- Coto (prioriza clásico)
        try:
            # 1) API VTEX por si responde
            prods_coto = vtex_api_products(ctx.request, coto_base, query, limit=limit, try_sc=True, extra_bases=[coto_alt])

            # 2) Si no, el clásico que sí carga en tu prueba
            if not prods_coto:
                prods_coto = coto_classic_search(page, query, sucursal_id=COTO_SUCURSAL_ID, limit=limit)

            out["coto"] = prods_coto or []
        except Exception as e:
            out["coto"] = [{"error": str(e)}]

        ctx.close(); browser.close()
        return out

# =========================
# Runner
# =========================

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "arroz gallo oro"
    res = buscar_producto(q, limit=3, headless=True, COTO_SUCURSAL_ID=200)
    print(json.dumps(res, ensure_ascii=False, indent=2))
