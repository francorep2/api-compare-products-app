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

# Importa el manager del browser
from app.core.browser_async import browser_manager_async
# Importa el tipo Page para type hinting
from playwright.async_api import Page, BrowserContext

# --- Stub de PROMO_NORMALIZER (ya que no tengo ese archivo) ---
# Si tienes 'scrapers._utils_promos', descomenta la siguiente línea:
# from scrapers._utils_promos import PROMO_NORMALIZER
# Si no, usa este stub temporal:
PROMO_NORMALIZER = re.compile(r"(?:\d+% (?:DTO|OFF))|(?:\d+x\d+)", re.IGNORECASE)
# -----------------------------------------------------------

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

# --- TIMEOUTS OPTIMIZADOS PARA KOYEB FREE TIER ---

# 1. PRESUPUESTO GLOBAL
# Si el scraper (incluyendo el "arranque en frío") tarda más que esto,
# se cancelará a sí mismo para evitar un 504.
SCRAPER_BUDGET_SEC = 9.0 

# 2. TIMEOUTS INTERNOS (Agresivos)
DEFAULT_GOTO_TIMEOUT_MS = 8000      # 8s para cargar la página
DEFAULT_SELECTOR_TIMEOUT_MS = 5000  # 5s para encontrar un elemento
DEFAULT_PDP_TIMEOUT_MS = 10000      # 10s para cargar una página de producto

# ===================================================
# A PARTIR DE AQUÍ ESTÁN TUS FUNCIONES ORIGINALES
# (Con modificaciones mínimas)
# ===================================================

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q)
    must = []
    if "cofler" in s: must.append("cofler")
    for k in ("galletita", "galletitas", "cookie", "cookies"):
        if k in s:
            must.append(k)
            break
    for k in ("chocolate", "chips"):
        if k in s: must.append(k)
    ok = sum(1 for m in must if m in t)
    return ok >= max(2, len(must) - 1)

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = text.replace("\xa0", " ").strip()
    cand = []
    for ent, dec in re.findall(r'(?:\$|ARS)\s*([0-9]{1,3}(?:[.\s][0-9]{3})+|[0-9]{4,})(?:,([0-9]{2}))?', t, flags=re.I):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{ent_clean}.{dec or '00'}"))
        except: pass
    for ent, dec in re.findall(r'(?:\$|ARS)\s*([0-9]{1,4})\s+([0-9]{2})', t, flags=re.I):
        try: cand.append(float(f"{int(ent)}.{dec}"))
        except: pass
    for ent, dec in re.findall(r'([0-9]{1,3}(?:[.\s][0-9]{3})*|[0-9]{1,4}),([0-9]{2})', t):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{int(ent_clean)}.{dec}"))
        except: pass
    for ent in re.findall(r'\b([0-9]{4,})\b', t):
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

def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if 8 <= len(d) <= 14 else None

def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

async def _compute_discount_from_prices(page) -> Optional[str]:
    # (Tu función original)
    try:
        html = await page.content()
        m_reg = re.search(r'Precio\s+regular\s*:\s*\$?\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        m_all = re.findall(r'\$\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        if m_reg and m_all:
            ent_reg, dec_reg = m_reg.group(1), m_reg.group(2)
            reg = float(ent_reg.replace(".", "").replace(" ", "") + "." + dec_reg)
            candidates = []
            for ent, dec in m_all:
                try:
                    val = float(ent.replace(".", "").replace(" ", "") + "." + dec)
                    candidates.append(val)
                except:
                    pass
            if not candidates: return None
            current = min((v for v in candidates if v < reg), default=None)
            if current is None: current = min(candidates)
            if reg > 0 and current is not None and current < reg:
                pct = round((reg - current) / reg * 100)
                if 1 <= pct <= 95: return f"{pct}% DTO"
    except Exception:
        pass
    return None

GRID_BADGE_SELECTORS = [
    "[class*='flagName']", "[class*='flagText']", "[class*='productHighlightText']",
    "[class*='discount']", "[data-testid*='discount']", "[data-testid*='badge']",
    ".badge", ".flags", ".tag", ".promo", ".promotion", ".product-flag",
    "span:has-text('OFERTA')", "span:has-text('Dto')", "strong:has-text('OFERTA')"
]

def _pick_best_promo_text(text: str) -> Optional[str]:
    # (Tu función original)
    if not text: return None
    txt = re.sub(r"\s+", " ", text).strip()
    m = PROMO_NORMALIZER.search(txt)
    if not m: return None
    hit = m.group(0).upper().replace("  ", " ").strip()
    hit = hit.replace("DESCUENTO", "DTO").replace("%OFF", "% OFF")
    return hit

async def extract_promotion_from_dom_page(page) -> Optional[str]:
    # (Tu función original)
    COTO_BADGE_SELECTORS = [
        ".promo", ".promotion", ".badge", ".flags", ".product-flag", ".ribbon",
        "[class*='promo']", "[class*='Promotion']", "[class*='badge']",
        ".descuento", ".beneficio", ".ahorro", ".etiqueta", ".label",
        ".tag", ".tag-oferta", ".tag-descuento", ".product__badge",
        ".prod__promo", ".prod__flag", ".product-promotion",
        "span:has-text('OFERTA')", "strong:has-text('OFERTA')", "span:has-text('Dto')"
    ]
    def _try_pick(txts: list[str]) -> Optional[str]:
        txt = " | ".join(t for t in txts if t)
        m_pct = re.search(r'\b(\d{1,3})\s*%', txt, flags=re.I)
        if m_pct: return f"{m_pct.group(1)}% DTO"
        return _pick_best_promo_text(txt)
    for sel in COTO_BADGE_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count():
                texts = [t.strip() for t in await loc.all_inner_texts() if t and t.strip()]
                try:
                    first = loc.first
                    for attr in ("aria-label", "title"):
                        val = await first.get_attribute(attr)
                        if val: texts.append(val.strip())
                except: pass
                promo = _try_pick(texts)
                if promo: return promo
        except: continue
    try:
        price_zone = page.locator("xpath=//*[contains(@class,'price') or contains(@class,'precio') or contains(@class,'product-price')][1]")
        if await price_zone.count():
            texts = [t.strip() for t in await price_zone.all_inner_texts() if t and t.strip()]
            promo = _try_pick(texts)
            if promo: return promo
    except: pass
    calc = await _compute_discount_from_prices(page)
    if calc: return calc
    try:
        body_txt = await page.inner_text("body")
        return _try_pick([body_txt])
    except: return None

async def extract_promotion_from_card(card_locator) -> Optional[str]:
    # (Tu función original)
    try:
        card = card_locator.locator("xpath=ancestor::*[contains(@class,'product') or contains(@class,'summary') or contains(@class,'grid')][1]")
    except:
        card = card_locator
    for sel in GRID_BADGE_SELECTORS:
        try:
            loc = card.locator(sel)
            if await loc.count():
                texts = [t.strip() for t in await loc.all_inner_texts() if t.strip()]
                if texts:
                    compact = " | ".join(dict.fromkeys(texts))
                    best = _pick_best_promo_text(compact)
                    if best: return best
        except: continue
    return None

async def parse_jsonld_product(page):
    # (Tu función original)
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
        except:
            pass
    return None

async def parse_meta_price_dom(page):
    # (Tu función original)
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
    except:
        pass
    return None

async def parse_regex_from_html(page) -> Optional[str]:
    # (Tu función original)
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

async def extract_static_image_from_html(page):
    html = await page.content()
    m = STATIC_IMG_REGEX.search(html)
    return m.group(0) if m else None

async def extract_image_url(page, final_url) -> Optional[str]:
    # (Tu función original)
    url = await extract_static_image_from_html(page)
    if url: return url
    sku = sku_from_url(final_url)
    if sku:
        built = build_static_image_url(sku, size="large")
        if built: return built
    try:
        jl = await parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(COTO_BASE, jl["image"])
    except: pass
    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if await el.count():
            src = await el.get_attribute("content")
            if src and "cotodigital" in src:
                return absolutize(COTO_BASE, src)
    el = page.locator("link[rel='image_src']").first
    if await el.count():
        src = await el.get_attribute("href")
        if src and "cotodigital" in src:
            return absolutize(COTO_BASE, src)
    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if await el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original"]:
                src = await el.get_attribute(attr)
                if src and "cotodigital" in src:
                    return absolutize(COTO_BASE, src)
    return None

async def extract_barcode_from_datalayer_or_html(page) -> Optional[str]:
    # (Tu función original)
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

def sanitize_coto_href(href:str) -> str:
    # (Tu función original)
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

# ===================================================
# A PARTIR DE AQUÍ ESTÁN LAS FUNCIONES OPTIMIZADAS
# ===================================================

async def fetch_coto_pdp_optimized(page: Page, href: str, timeout: int = DEFAULT_PDP_TIMEOUT_MS) -> Dict:
    """
    Versión optimizada de fetch_coto_pdp.
    RECIBE una página, no la crea.
    NO la cierra al finalizar.
    """
    try:
        url = absolutize(COTO_BASE, href)
        url = sanitize_coto_href(url)
        print(f"[LOG] PDP GET {url}")
        
        # Usamos el timeout asignado
        await page.goto(url, wait_until="load", timeout=timeout) 

        title = None
        for sel in ["h1[itemprop='name']", "h1.product-name", "h1", ".product-name"]:
            el = page.locator(sel).first
            if await el.count():
                t = clean_text(await el.inner_text())
                if t and len(t) > 2:
                    title = t
                    break

        price = None
        jl = await parse_jsonld_product(page)
        if jl:
            price = jl.get("price") or price
            if jl.get("title") and not title:
                title = jl["title"]
        if not price: price = await parse_meta_price_dom(page)
        if not price: price = await parse_regex_from_html(page)
        if not title: title = clean_text(await page.title())

        image_url = await extract_image_url(page, url)
        barcode = jl.get("barcode") if jl and jl.get("barcode") else None
        if not barcode:
            barcode = await extract_barcode_from_datalayer_or_html(page)
        promotion = await extract_promotion_from_dom_page(page)

        return {
            "title": title, "price": price, "url": url, "image_url": image_url,
            "barcode": barcode, "promotion": promotion,
        }
    except Exception as e:
        print(f"[LOG] PDP error Coto: {e}")
        # await save_debug_html(page, "coto_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None, "promotion": None}
    # ¡IMPORTANTE! No cerramos la página aquí

async def coto_search_fast_mode(ctx: BrowserContext, query: str, limit: int = TOP_N) -> List[Dict]:
    """
    MODO SUPER RÁPIDO: Solo busca, extrae links y títulos de la grilla. NO entra a PDPs.
    """
    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={urllib.parse.quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )
    
    page = await ctx.new_page()
    # await _enable_speed_routes(page) # Podrías habilitar esto para bloquear CSS/JS si aún falla

    try:
        print(f"[LOG] Coto FAST MODE GET {search_url}")
        # Usamos el timeout de goto
        await page.goto(search_url, wait_until="domcontentloaded", timeout=DEFAULT_GOTO_TIMEOUT_MS) 

        anchors_selector = "a[href*='/sitios/cdigi/productos/']"
        # Usamos el timeout de selector
        await page.wait_for_selector(anchors_selector, timeout=DEFAULT_SELECTOR_TIMEOUT_MS) 
        
        # Opcional: Scroll ligero por si los productos cargan con delay
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(0.1)

        # Extraer datos directamente de la grilla con JS (más rápido)
        card_data = await page.eval_on_selector_all(
            anchors_selector,
            """(anchors, limit) => {
                const results = [];
                const seenUrls = new Set();
                for (let a of anchors) {
                    if (results.length >= limit) break;
                    
                    const href = a.getAttribute('href');
                    if (!href) continue;
                    
                    // Simple dedupe based on href before normalization
                    if (seenUrls.has(href)) continue;
                    seenUrls.add(href);

                    const card = a.closest('[class*="product"]');
                    let title = null;
                    if (card) {
                        // Intentamos buscar varios selectores de título
                        const titleEl = card.querySelector('[class*="product_title"]') || 
                                        card.querySelector('[class*="productName"]') ||
                                        card.querySelector('[data-testid="product-title"]') || 
                                        card.querySelector('h3');
                        if (titleEl) title = titleEl.textContent;
                    }
                    
                    results.push({ href: href, title: title });
                }
                return results;
            }""",
            limit # Pasamos el límite a la función JS
        )

        # Mapear a tu template final
        results: List[Dict] = []
        seen_final_urls = set()
        for item in card_data:
            if len(results) >= limit: break # Doble check del límite

            full_url = absolutize(COTO_BASE, item.get('href'))
            if not full_url or full_url in seen_final_urls:
                continue
            seen_final_urls.add(full_url)
            
            # Intentar sacar Barcode/SKU de la URL
            barcode = None
            m = re.search(r'-([0-9]{8,14})(?:-|$)', full_url)
            if m:
                barcode = normalize_barcode(m.group(1)) # Usa tu función normalize_barcode

            results.append({
                "title": clean_text(item.get('title')), # Usa tu función clean_text
                "price": None, # No disponible en modo rápido
                "url": full_url,
                "image_url": None, # No disponible en modo rápido
                "barcode": barcode, # Mejor que nada
                "promotion": None # No disponible en modo rápido
            })

        print(f"[LOG] Coto FAST MODE encontró {len(results)} items.")
        return results

    except Exception as e:
        # Esto incluye Playwright TimeoutError si page.goto o wait_for_selector fallan
        print(f"[LOG] Coto FAST MODE search error: {e}")
        # await save_debug_html(page, "coto_fast_error") # Descomenta para debug local
        return []
    finally:
        await page.close()


async def coto_search_by_barcode(ctx: BrowserContext, ean: str, limit: int = TOP_N) -> List[Dict]:
    """ Busca por barcode usando el modo rápido. """
    # En modo rápido, no podemos verificar el barcode DENTRO del PDP.
    # Simplemente buscamos el EAN como texto y devolvemos los resultados.
    return await coto_search_fast_mode(ctx, ean, limit=limit)

# ===================================================
# API PÚBLICA (Usa el modo rápido y el timeout global)
# ===================================================
async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    query_input = barcode if barcode else q
    if not query_input or not query_input.strip():
        print("[COTO LOG] Search query is empty. Returning [].")
        return []
        
    query = query_input.strip()
    is_barcode_search = bool(barcode and normalize_barcode(query)) # Usa normalize_barcode para chequear

    print(f"[COTO LOG] Iniciando búsqueda FAST MODE con budget {SCRAPER_BUDGET_SEC}s para: '{query}' (IsBarcode: {is_barcode_search})")

    try:
        # --- TIMEOUT GLOBAL ---
        async with asyncio.timeout(SCRAPER_BUDGET_SEC):
            # --- OBTENER CONTEXTO ---
            async with browser_manager_async.context() as ctx:
                # --- LLAMAR AL SCRAPER MODO RÁPIDO ---
                # Ya sea búsqueda por texto o barcode, usamos la misma función rápida
                return await coto_search_fast_mode(ctx, query, limit=limit)
                
    except asyncio.TimeoutError:
        print(f"[COTO LOG] TIMEOUT GLOBAL ({SCRAPER_BUDGET_SEC}s) reached for: '{query}'. Returning empty list.")
        return []
        
    except Exception as e:
        print(f"[COTO ERROR] Critical failure in search function for '{query}': {e}")
        import traceback
        traceback.print_exc() 
        return []