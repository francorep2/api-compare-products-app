# scrapers_async/coto_scraper_async.py
import json
import urllib.parse
import re
import time
import datetime
import os
import asyncio
import unicodedata
import contextlib
from typing import List, Dict, Optional, Any

from app.core.browser_async import browser_manager_async
from playwright.async_api import Page, BrowserContext, Locator

try:
    from scrapers._utils_promos import PROMO_NORMALIZER
except ImportError:
    PROMO_NORMALIZER = re.compile(r"(?:\d+% (?:DTO|OFF))|(?:\d+x\d+)", re.IGNORECASE)

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

# --- TIMEOUTS AJUSTADOS PARA eSmall (CON PDPs) ---
SCRAPER_BUDGET_SEC = 25.0 # Presupuesto global extendido
DEFAULT_GOTO_TIMEOUT_MS = 15000     # 15s para cargar página principal/búsqueda
DEFAULT_SELECTOR_TIMEOUT_MS = 7000  # 7s para encontrar elementos
DEFAULT_PDP_TIMEOUT_MS = 18000     # 18s para cargar CADA página de producto

# ===================== Utils (Helpers) =====================

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: Optional[str], q: str) -> bool:
    if not title: return False
    t = _norm_text(title)
    s = _norm_text(q)
    query_words = s.split()
    return all(word in t for word in query_words)

def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = str(text).replace("\xa0", " ").strip()
    cand = []
    patterns = [
        r'(?:\$|ARS)\s*([0-9]{1,3}(?:[.\s][0-9]{3})*|[0-9]+)(?:,([0-9]{2}))?',
        r'(?:\$|ARS)\s*([0-9]{1,4})\s+([0-9]{2})\b',
        r'\b([0-9]{1,3}(?:[.\s][0-9]{3})*|[0-9]+),([0-9]{2})\b'
    ]
    for pattern in patterns:
        for ent, dec in re.findall(pattern, t, flags=re.I):
            ent_clean = ent.replace(".", "").replace(" ", "")
            try:
                if not dec and len(ent_clean) >= 4 and '.' not in ent_clean:
                     cand.append(float(ent_clean))
                else:
                     cand.append(float(f"{ent_clean}.{dec or '00'}"))
            except ValueError: pass

    for ent in re.findall(r'\b([0-9]{4,})\b', t):
         if not (8 <= len(ent) <= 14):
             try: cand.append(float(int(ent)))
             except ValueError: pass

    if not cand: return None
    return f"ARS {max(cand):.2f}"

async def save_debug_html(page: Page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        html = await page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html)
        print(f"[LOG] Saved debug HTML: {path}")
    except Exception as e:
        print(f"[WARN] Could not save debug HTML: {e}")

def absolutize(base: str, href: Optional[str], current_url: Optional[str] = None) -> Optional[str]:
    if not href: return current_url or base
    try:
        return urllib.parse.urljoin(current_url or base, href)
    except ValueError:
        return href

def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if d and (len(d) == 8 or 12 <= len(d) <= 14) else None

def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

async def _compute_discount_from_prices(page: Page) -> Optional[str]:
    try:
        html = await page.content()
        m_reg = re.search(r'Precio\s+regular\s*:\s*\$?\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        m_all = re.findall(r'\$\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        if m_reg and m_all:
            ent_reg, dec_reg = m_reg.group(1), m_reg.group(2)
            reg = float(ent_reg.replace(".", "").replace(" ", "") + "." + dec_reg)
            candidates = []
            for ent, dec in m_all:
                try: candidates.append(float(ent.replace(".", "").replace(" ", "") + "." + dec))
                except ValueError: pass

            if not candidates: return None
            current = min((v for v in candidates if v < reg), default=None)
            if current is None: current = min(candidates)
            if reg > 0 and current is not None and current < reg:
                pct = round((reg - current) / reg * 100)
                if 1 <= pct <= 95: return f"{pct}% DTO"
    except Exception as e:
        print(f"[WARN] Failed to compute discount from prices: {e}")
    return None

GRID_BADGE_SELECTORS = [
    "[class*='flagName']", "[class*='flagText']", "[class*='productHighlightText']",
    "[class*='discount']", "[data-testid*='discount']", "[data-testid*='badge']",
    ".badge", ".flags", ".tag", ".promo", ".promotion", ".product-flag",
    "span:has-text('OFERTA')", "span:has-text('Dto')", "strong:has-text('OFERTA')"
]

def _pick_best_promo_text(text: str) -> Optional[str]:
    if not text: return None
    txt = re.sub(r"\s+", " ", text).strip()
    m = PROMO_NORMALIZER.search(txt)
    if not m: return None
    hit = m.group(0).upper().replace("  ", " ").strip()
    hit = hit.replace("DESCUENTO", "DTO").replace("%OFF", "% OFF")
    return hit

async def extract_promotion_from_card(card_locator: Locator) -> Optional[str]:
    try:
        card = card_locator.locator("xpath=ancestor::*[contains(@class,'product') or contains(@class,'summary') or contains(@class,'grid')][1]")
    except Exception:
        card = card_locator

    texts = []
    for sel in GRID_BADGE_SELECTORS:
        try:
            loc = card.locator(sel)
            count = await loc.count()
            if count > 0:
                all_texts = await loc.all_inner_texts()
                texts.extend(t.strip() for t in all_texts if t and t.strip())
        except Exception:
            continue

    if not texts: return None
    unique_texts = " | ".join(dict.fromkeys(texts))
    return _pick_best_promo_text(unique_texts)

async def extract_promotion_from_dom_page(page: Page) -> Optional[str]:
    COTO_BADGE_SELECTORS = [
         ".promo", ".promotion", ".badge", ".flags", ".product-flag", ".ribbon",
         "[class*='promo']", "[class*='Promotion']", "[class*='badge']",
         ".descuento", ".beneficio", ".ahorro", ".etiqueta", ".label",
         ".tag", ".tag-oferta", ".tag-descuento", ".product__badge",
         ".prod__promo", ".prod__flag", ".product-promotion",
         "span:has-text('OFERTA')", "strong:has-text('OFERTA')", "span:has-text('Dto')"
    ]
    def _try_pick(txts: list[str]) -> Optional[str]:
        if not txts: return None
        txt = " | ".join(dict.fromkeys(t for t in txts if t))
        m_pct = re.search(r'\b(\d{1,3})\s*%', txt, flags=re.I)
        if m_pct: return f"{m_pct.group(1)}% DTO"
        return _pick_best_promo_text(txt)

    all_texts = []
    for sel in COTO_BADGE_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count(timeout=500) > 0:
                all_texts.extend(t.strip() for t in await loc.all_inner_texts() if t and t.strip())
                try:
                    first = loc.first
                    for attr in ("aria-label", "title"):
                        val = await first.get_attribute(attr, timeout=100)
                        if val: all_texts.append(val.strip())
                except Exception: pass
        except Exception: continue

    promo = _try_pick(all_texts)
    if promo: return promo

    try:
        price_zone = page.locator("xpath=//*[contains(@class,'price') or contains(@class,'precio') or contains(@class,'product-price')][1]")
        if await price_zone.count(timeout=500) > 0:
            pz_texts = [t.strip() for t in await price_zone.all_inner_texts() if t and t.strip()]
            promo = _try_pick(pz_texts)
            if promo: return promo
    except Exception: pass

    calc = await _compute_discount_from_prices(page)
    if calc: return calc

    try:
        body_txt = await page.locator('body').inner_text(timeout=1000)
        promo = _try_pick([body_txt[:2000]])
        if promo: return promo
    except Exception: pass

    return None


async def parse_jsonld_product(page: Page) -> Optional[Dict[str, Any]]:
    scripts = page.locator("script[type='application/ld+json']")
    count = await scripts.count()
    for i in range(count):
        try:
            raw = (await scripts.nth(i).inner_text(timeout=500)).strip()
            if not raw: continue
            data = json.loads(raw)

            items_to_check = []
            if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
                items_to_check.extend(data["@graph"])
            elif isinstance(data, list):
                 items_to_check.extend(data)
            elif isinstance(data, dict):
                 items_to_check.append(data)

            for item in items_to_check:
                if not isinstance(item, dict): continue

                item_type = item.get("@type", "")
                is_product = "Product" in (item_type if isinstance(item_type, str) else str(item_type))

                if is_product:
                    name = item.get("name")
                    offers_data = item.get("offers")
                    price = None

                    offers = None
                    if isinstance(offers_data, list) and offers_data:
                        offers = offers_data[0]
                    elif isinstance(offers_data, dict):
                        offers = offers_data

                    if isinstance(offers, dict):
                        p_val = offers.get("price") or (offers.get("priceSpecification") or {}).get("price")
                        if p_val:
                            try: price_float = float(str(p_val).replace(",", "."))
                            except ValueError: price_float = None
                            if price_float is not None:
                                price = f"ARS {price_float:.2f}"

                    image_data = item.get("image")
                    image_url = None
                    if isinstance(image_data, list) and image_data:
                        img_obj = image_data[0]
                        image_url = img_obj if isinstance(img_obj, str) else (img_obj.get('url') if isinstance(img_obj, dict) else None)
                    elif isinstance(image_data, str):
                         image_url = image_data
                    elif isinstance(image_data, dict):
                         image_url = image_data.get('url')

                    barcode = choose_barcode(item.get("gtin13"), item.get("gtin14"), item.get("gtin"), item.get("sku"))

                    return {
                        "title": clean_text(name) if name else None,
                        "price": price,
                        "image": image_url,
                        "barcode": barcode,
                    }
        except Exception:
            pass
    return None


async def parse_meta_price_dom(page: Page) -> Optional[str]:
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        try:
            el = page.locator(sel).first
            if await el.count(timeout=500) > 0:
                val = await el.get_attribute("content")
                if val:
                    try: return f"ARS {float(str(val).replace(',','.')):.2f}"
                    except ValueError: pass
        except Exception: pass

    for cont_sel in ["#skuBestPrice", ".skuBestPrice", ".best-price", ".bestPrice", ".price", ".new-price"]:
         try:
            cont = page.locator(cont_sel).first
            if await cont.count(timeout=500) > 0:
                price_text = await cont.inner_text()
                pr = normalize_price(price_text)
                if pr: return pr
         except Exception: pass

    for sel in ["span:has-text('$')", "div:has-text('$')"]:
         try:
            els = page.locator(sel).locator('visible=true')
            if await els.count(timeout=500) > 0:
                 pr = normalize_price(await els.first.inner_text())
                 if pr: return pr
         except Exception: pass

    try:
        has_sku_json = await page.evaluate("() => typeof window.skuJson === 'object' && window.skuJson !== null", timeout=500)
        if has_sku_json:
            data = await page.evaluate("() => window.skuJson")
            if isinstance(data, dict) and isinstance(data.get("skus"), list):
                for s in data["skus"]:
                    if isinstance(s, dict) and s.get("bestPrice"):
                        try: return f"ARS {float(s['bestPrice'])/100:.2f}"
                        except (ValueError, TypeError): pass
    except Exception: pass

    return None


async def parse_regex_from_html(page: Page) -> Optional[str]:
    try:
        html = await page.content()
        matches = re.findall(r"(?:Precio|ARS|\$)\s*([\d.,\s]+)", html, flags=re.I)
        best_price_str = None
        max_price_val = 0.0

        for match in matches:
             price_str = normalize_price(match)
             if price_str:
                 try:
                     price_val = float(price_str.replace("ARS", "").strip())
                     if price_val > max_price_val:
                         max_price_val = price_val
                         best_price_str = price_str
                 except ValueError: pass

        return best_price_str
    except Exception as e:
        print(f"[WARN] Regex price parsing failed: {e}")
        return None

STATIC_IMG_REGEX = re.compile(
    r"https?://static\.cotodigital(?:3)?\.com\.ar/sitios/fotos/(?:large|medium|small)/\d{8}/\d{8}\.jpg",
    re.IGNORECASE
)

def sku_from_url(url: Optional[str]) -> Optional[str]:
    if not url: return None
    m = re.search(r"/R-(\d{8})-", url)
    return m.group(1) if m else None

def build_static_image_url(sku: str, size="large") -> Optional[str]:
    if not sku or len(sku) != 8: return None
    folder = sku[:6] + "00"
    return f"{COTO_STATIC_HOST}/sitios/fotos/{size}/{folder}/{sku}.jpg"

async def extract_static_image_from_html(page: Page) -> Optional[str]:
    try:
        html = await page.content()
        m = STATIC_IMG_REGEX.search(html)
        return m.group(0) if m else None
    except Exception: return None

async def extract_image_url(page: Page, final_url: Optional[str]) -> Optional[str]:
    static_url = await extract_static_image_from_html(page)
    if static_url: return static_url

    sku = sku_from_url(final_url)
    if sku:
        built_url = build_static_image_url(sku, size="large")
        if built_url: return built_url

    try:
        jl = await parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(COTO_BASE, jl["image"], final_url)
    except Exception: pass

    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']", "link[rel='image_src']"]:
        try:
             el = page.locator(sel).first
             if await el.count(timeout=500) > 0:
                 attr = "content" if sel.startswith("meta") else "href"
                 src = await el.get_attribute(attr)
                 # Añadir chequeo básico para evitar data URIs o URLs inválidas
                 if src and src.startswith(('http', '/')):
                      abs_url = absolutize(COTO_BASE, src, final_url)
                      # Opcional: chequear si la URL parece válida antes de devolver
                      if abs_url and 'cotodigital' in abs_url:
                           return abs_url
        except Exception: pass

    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "#image-main", "img[class*='product']"]: # Selectores más específicos primero
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(min(count, 3)): # Chequear las primeras 3 imágenes
                 el = els.nth(i)
                 # Priorizar 'src', luego 'data-src' etc.
                 for attr in ["src", "data-src", "data-lazy", "data-original"]:
                     src = await el.get_attribute(attr, timeout=100)
                     if src and src.startswith(('http', '/')) and 'base64' not in src:
                          abs_url = absolutize(COTO_BASE, src.split(',')[0].strip(), final_url) # Tomar primera URL si hay srcset
                          if abs_url and 'cotodigital' in abs_url:
                               return abs_url
        except Exception: pass
        
    print(f"[WARN] No image URL found for {final_url}")
    return None

async def extract_barcode_from_datalayer_or_html(page: Page) -> Optional[str]:
    try:
        has_dl = await page.evaluate("() => Array.isArray(window.dataLayer)")
        if has_dl:
            arr = await page.evaluate("() => window.dataLayer")
            for item in reversed(arr): # Buscar desde el final (eventos más recientes)
                if isinstance(item, dict):
                    # Buscar recursivamente en el diccionario
                    stack = [item]
                    while stack:
                        current = stack.pop()
                        if isinstance(current, dict):
                             for k, v in current.items():
                                 if k.lower() in ("ean", "gtin", "gtin13", "barcode"):
                                     bc = normalize_barcode(str(v))
                                     if bc: return bc
                                 if isinstance(v, (dict, list)):
                                     stack.append(v)
                        elif isinstance(current, list):
                            stack.extend(current)

    except Exception as e:
        print(f"[WARN] Error reading dataLayer: {e}")

    try:
        html = await page.content()
        # Buscar EAN en JSON-LD (ya lo hace parse_jsonld_product, pero como fallback)
        for raw in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, flags=re.S|re.I):
            try:
                data = json.loads(raw)
                # ... (lógica de buscar en JSON-LD, similar a parse_jsonld_product) ...
                stack = [data]
                while stack:
                    item = stack.pop()
                    if isinstance(item, dict):
                        if "Product" in str(item.get("@type")):
                             bc = choose_barcode(item.get("gtin13"), item.get("gtin14"), item.get("gtin"), item.get("sku"))
                             if bc: return bc
                        stack.extend(item.values())
                    elif isinstance(item, list):
                         stack.extend(item)
            except Exception: continue

        # Buscar números EAN-like directamente en el HTML
        nums = re.findall(r'\b(\d{8}|\d{12,14})\b', html)
        for n in nums:
             if normalize_barcode(n): return n # Devuelve el primero válido

    except Exception as e:
        print(f"[WARN] Error extracting barcode from HTML: {e}")

    return None

def sanitize_coto_href(href:str) -> str:
    try:
        s = urllib.parse.unquote(href)
        # Quitar parámetros de tracking comunes y parámetros duplicados
        parsed = urllib.parse.urlparse(s)
        query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        # Parámetros a mantener (y su orden)
        keep_params = ['Dy', 'idSucursal', 'Ntt'] # Añade otros si son necesarios
        final_q = {}
        
        # Asegurar Dy e idSucursal
        final_q['Dy'] = query_params.get('Dy', ['1'])[0]
        final_q['idSucursal'] = query_params.get('idSucursal', [str(COTO_SUCURSAL_ID)])[0]

        # Añadir Ntt si existe
        if 'Ntt' in query_params:
            final_q['Ntt'] = query_params['Ntt'][0]
        
        # Añadir otros parámetros importantes si los hubiera
        # for k in other_important_params:
        #    if k in query_params: final_q[k] = query_params[k][0]

        new_query = urllib.parse.urlencode(final_q, doseq=False)
        
        # Reconstruir URL limpia
        # Quitar '/_/' si existe al final del path
        clean_path = parsed.path.removesuffix('/_/') if parsed.path else '/'
        
        return urllib.parse.urlunparse((parsed.scheme or 'https', parsed.netloc, clean_path, parsed.params, new_query, parsed.fragment))

    except Exception as e:
        print(f"[WARN] Failed to sanitize href {href}: {e}")
        return href # Devolver original si falla

# ===================================================
# FUNCIONES DE SCRAPING (Con visita a PDP)
# ===================================================

async def fetch_coto_pdp(page: Page, href: str, timeout: int = DEFAULT_PDP_TIMEOUT_MS) -> Dict:
    result = {"title": None, "price": None, "url": href, "image_url": None, "barcode": None, "promotion": None}
    
    try:
        url = absolutize(COTO_BASE, href)
        if not url: raise ValueError("Invalid href")
        url = sanitize_coto_href(url)
        result["url"] = url 

        print(f"[LOG] PDP GET {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await asyncio.sleep(0.5) # Espera breve para JS

        json_ld_data = await parse_jsonld_product(page)
        if json_ld_data:
            result["title"] = result["title"] or json_ld_data.get("title")
            result["price"] = result["price"] or json_ld_data.get("price")
            img_jl = json_ld_data.get("image")
            if img_jl: result["image_url"] = result["image_url"] or absolutize(COTO_BASE, img_jl, url)
            result["barcode"] = result["barcode"] or json_ld_data.get("barcode")

        if not result["title"]:
            for sel in ["h1[itemprop='name']", "h1.product-name", "h1", ".product-name", "meta[property='og:title']"]:
                try:
                    el = page.locator(sel).first
                    if await el.count(timeout=500) > 0:
                        t = await el.get_attribute("content") if sel.startswith("meta") else await el.inner_text()
                        cleaned_title = clean_text(t)
                        if cleaned_title and len(cleaned_title) > 2:
                            result["title"] = cleaned_title
                            break
                except Exception: continue
        if not result["title"]:
             try: result["title"] = clean_text(await page.title())
             except Exception: pass

        if not result["price"]:
            result["price"] = await parse_meta_price_dom(page)
        if not result["price"]:
             result["price"] = await parse_regex_from_html(page)

        if not result["image_url"]:
            result["image_url"] = await extract_image_url(page, url)

        if not result["barcode"]:
            result["barcode"] = await extract_barcode_from_datalayer_or_html(page)

        if not result["promotion"]:
            result["promotion"] = await extract_promotion_from_dom_page(page)

        print(f"[LOG] PDP OK: {result.get('title') or 'N/A'} - {result.get('price') or 'N/A'}")
        return result

    except Exception as e:
        print(f"[LOG] PDP error Coto for {href}: {e}")
        # await save_debug_html(page, "coto_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None, "promotion": None}


async def coto_search_top_n(ctx: BrowserContext, query: str, limit: int = TOP_N) -> List[Dict]:
    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={urllib.parse.quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )

    page = await ctx.new_page()
    pdp_page = await ctx.new_page()
    # await _enable_speed_routes(page) # Opcional
    # await _enable_speed_routes(pdp_page) # Opcional

    results: List[Dict] = []
    processed_urls = set()

    try:
        print(f"[LOG] Coto Search GET {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=DEFAULT_GOTO_TIMEOUT_MS)

        anchors_selector = "a[href*='/sitios/cdigi/productos/']"
        await page.wait_for_selector(anchors_selector, timeout=DEFAULT_SELECTOR_TIMEOUT_MS)

        await page.mouse.wheel(0, 500)
        await asyncio.sleep(0.2)

        anchors = page.locator(anchors_selector)
        total_found = await anchors.count()
        print(f"[LOG] Found {total_found} potential product links.")
        if not total_found:
            return []

        for i in range(min(total_found, limit * 4)):
            if len(results) >= limit:
                print(f"[LOG] Reached limit ({limit}). Stopping PDP processing.")
                break

            a = anchors.nth(i)
            href = await a.get_attribute("href")
            if not href: continue

            try:
                full_href = absolutize(COTO_BASE, href, search_url)
                if not full_href: continue
                full_href = sanitize_coto_href(full_href)
            except Exception:
                continue

            if full_href in processed_urls:
                continue
            processed_urls.add(full_href)

            card_promo = await extract_promotion_from_card(a)

            try:
                pdp_data = await fetch_coto_pdp(pdp_page, full_href)

                if pdp_data.get("promotion") is None and card_promo:
                    pdp_data["promotion"] = card_promo

                if pdp_data and pdp_data.get("title") and pdp_data.get("price"):
                    # Chequeo final de relevancia (opcional pero bueno)
                    if _title_seems_related(pdp_data.get("title"), query):
                         results.append(pdp_data)
                         print(f"[LOG] Added valid PDP result #{len(results)}: {pdp_data.get('title')}")
                    else:
                         print(f"[LOG] Discarded PDP result (title mismatch): {pdp_data.get('title')}")
                else:
                    print(f"[LOG] Discarded PDP result (missing title/price): {full_href}")

            except Exception as e:
                print(f"[WARN] Error processing PDP for {full_href}: {e}")

        print(f"[LOG] Finished processing PDPs. Found {len(results)} valid items.")
        return results

    except Exception as e:
        print(f"[ERROR] Coto search failed: {e}")
        # await save_debug_html(page, "coto_search_error")
        return []
    finally:
        await page.close()
        await pdp_page.close()


async def coto_search_by_barcode(ctx: BrowserContext, ean: str, limit: int = TOP_N) -> List[Dict]:
    print(f"[LOG] Searching by barcode (EAN): {ean}")
    prelim_results = await coto_search_top_n(ctx, ean, limit=max(limit, 2))

    out = []
    for item in prelim_results:
        bc = item.get("barcode")
        # Priorizar coincidencia exacta de barcode
        if bc and bc == ean:
            out.append(item)
            if len(out) >= limit: break
    
    # Si no hubo coincidencia exacta pero la búsqueda trajo algo, devolver eso
    final_results = out if out else prelim_results
    print(f"[LOG] Barcode search for {ean} returning {len(final_results)} items.")
    return final_results[:limit]


# ===================================================
# API PÚBLICA (CON TIMEOUT GLOBAL)
# ===================================================
async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    query_input = barcode if barcode else q
    if not query_input or not query_input.strip():
        print("[COTO LOG] Search query is empty. Returning [].")
        return []

    query = query_input.strip()
    is_barcode_search = bool(barcode and normalize_barcode(barcode))

    print(f"[COTO LOG] Initiating search with budget {SCRAPER_BUDGET_SEC}s for: '{query}' (IsBarcode: {is_barcode_search})")

    try:
        async with asyncio.timeout(SCRAPER_BUDGET_SEC):
            async with browser_manager_async.context() as ctx:
                if is_barcode_search:
                    return await coto_search_by_barcode(ctx, query, limit=limit)
                else:
                    return await coto_search_top_n(ctx, query, limit=limit)

    except asyncio.TimeoutError:
        print(f"[COTO LOG] TIMEOUT GLOBAL ({SCRAPER_BUDGET_SEC}s) reached for: '{query}'. Returning empty list.")
        return []

    except Exception as e:
        print(f"[COTO ERROR] Critical failure in search function for '{query}': {e}")
        import traceback
        traceback.print_exc()
        return []