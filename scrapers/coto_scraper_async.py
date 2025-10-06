from typing import List, Dict, Optional
import json, urllib.parse, re, time, datetime, os, asyncio, unicodedata

import httpx
from scrapers._utils_promos import PROMO_NORMALIZER

# ===================== Config & Cache =====================
TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200
CONCURRENCY_LIMIT = 4
REQUEST_TIMEOUT = 8.0
HARD_TIME_BUDGET = 25.0 # Aumentado para dar más margen a Playwright
CACHE: Dict[str, tuple] = {}
CACHE_TTL_SECONDS = 3600  # 1 hora

# ===================== Texto / matching =====================
def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    if not q: return True
    t = _norm_text(title)
    s = _norm_text(q)
    for w in s.split():
        if w and w not in t:
            return False
    return True

# ===================== Utils comunes (sync) =====================
def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def _to_float(s: Optional[str]) -> Optional[float]:
    if not s: return None
    s2 = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except Exception:
        return None

def _ars(amount: Optional[float]) -> Optional[str]:
    if amount is None: return None
    val = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"ARS {val}"

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

async def save_debug_artefacts(page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(html_path, "w", encoding="utf-8") as f: f.write(await page.content())
        img_path = os.path.join("debug", f"{tag}_{ts}.png")
        await page.screenshot(path=img_path, full_page=True)
        print(f"[LOG] Guardados artefactos de depuración: {html_path} y {img_path}")
    except Exception as e:
        print(f"[LOG] No se pudieron guardar los artefactos de depuración: {e}")

# ===================== JSON FAST SEARCH (Mejorado) =====================
async def _fetch_coto_json(query: str) -> Optional[dict]:
    url = f"{COTO_BASE}/sitios/cdigi/categoria"
    params = {"Ntt": query, "format": "json", "Nf": "product.endDate|GTEQ+1.7093928E12"} # Filtro para productos vigentes
    headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[coto][json] fetch error: {e}")
        return None

def _extract_from_json(data: dict, q: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    stack = [data]
    products_raw = []
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "productList" in current and isinstance(current["productList"], list):
                products_raw = current["productList"]; break
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    if not products_raw: return []
    for prod in products_raw:
        if not isinstance(prod, dict): continue
        name = prod.get("name")
        if not name or not _title_seems_related(name, q): continue
        link_data = prod.get("link", {}); href = link_data.get("path"); url = f"{COTO_BASE}{href}" if href else None
        price_data = prod.get("price", {}); final_price = price_data.get("finalPrice") or price_data.get("listPrice")
        ean = prod.get("ean"); images = prod.get("images", [])
        image_url = images[0].get("path") if images else None
        if image_url and image_url.startswith("//"): image_url = "https:" + image_url
        out.append({"title": name, "price": f"ARS {final_price:.2f}" if final_price is not None else None,
                    "url": url, "image_url": image_url, "barcode": ean, "promotion": None})
        if len(out) >= limit: break
    return out

async def _search_json_top_n(q: str, limit: int) -> List[Dict]:
    data = await _fetch_coto_json(urllib.parse.quote(q, safe=""))
    if not data: return []
    items = _extract_from_json(data, q, limit)
    return items[:limit]

# ===================== PDP/Search (fallback DOM) =====================
async def fetch_coto_pdp(ctx, href: str, timeout=20000) -> Dict:
    page = await ctx.new_page()
    try:
        url = absolutize(COTO_BASE, href)
        print(f"[coto-pw] Visitando PDP: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        title = await page.locator("h1.product-name").first.inner_text(timeout=5000)
        price_text = await page.locator(".atg_store_newPrice").first.inner_text(timeout=5000)
        price = normalize_price(price_text)
        image_url = await page.locator(".product-image img").first.get_attribute("src")
        return {"title": title, "price": price, "url": url, "image_url": image_url}
    except Exception as e:
        print(f"[coto-pw] Error al procesar PDP {href}: {e}")
        await save_debug_artefacts(page, f"coto_pdp_error_{int(time.time())}")
        return {}
    finally:
        await page.close()

async def coto_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    page = await ctx.new_page()
    try:
        search_url = f"{COTO_BASE}/sitios/cdigi/busqueda?Ntt={urllib.parse.quote(query)}"
        print(f"[coto-pw] Navegando a: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)

        body_text = await page.inner_text('body')
        if "Verificar que no eres un robot" in body_text or "Enable JavaScript and cookies" in body_text:
            print("[coto-pw] BLOQUEADO: Se detectó página de CAPTCHA/bloqueo.")
            await save_debug_artefacts(page, "coto_blocked")
            return []

        await page.wait_for_selector("[id*='producto'] a", timeout=10000)
        
        product_links = await page.locator("[id*='producto'] a").all()
        hrefs = []
        seen_hrefs = set()
        for link in product_links[:limit*2]:
            href = await link.get_attribute("href")
            if href and href not in seen_hrefs:
                hrefs.append(href)
                seen_hrefs.add(href)
        
        if not hrefs: return []

        results = []
        for href in hrefs[:limit]:
            try:
                pdp_data = await fetch_coto_pdp(ctx, href)
                if pdp_data and pdp_data.get("title"):
                    results.append(pdp_data)
            except Exception as e:
                print(f"[coto-pw] Error en un PDP individual: {e}")
        
        return results
    except Exception as e:
        print(f"[coto-pw] Búsqueda con Playwright falló: {e}")
        await save_debug_artefacts(page, "coto_playwright_error")
        return []
    finally:
        await page.close()

async def coto_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    return await coto_search_top_n(ctx, ean, limit=limit)

# ===================== API pública del scraper =====================
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    start_time = time.time()
    query = q.strip() if q else barcode.strip() if barcode else ""
    if not query: return []
    
    cache_key = f"coto:{query}"
    cached_data = CACHE.get(cache_key)
    if cached_data:
        timestamp, results = cached_data
        if (time.time() - timestamp) < CACHE_TTL_SECONDS:
            print(f"[coto] Éxito con CACHE para '{query}'")
            return results[:limit]

    print(f"[coto] Cache MISS para '{query}'")
    
    ean = normalize_barcode(barcode or q)
    
    results = []
    if q and not ean:
        try:
            results = await _search_json_top_n(q, limit)
            if results:
                print(f"[coto] Éxito con búsqueda JSON para '{q}'.")
        except Exception as e:
            print(f"[coto] Búsqueda por JSON falló: {e}, intentando con Playwright...")

    if not results:
        query_to_use = ean if ean and (not q or q.strip() == ean) else (q.strip() if q else "")
        if query_to_use:
            results = await coto_search_top_n(ctx, query_to_use, limit=limit)

    if results:
        CACHE[cache_key] = (time.time(), results)

    end_time = time.time()
    print(f"[coto] Búsqueda para '{query}' completada en {end_time - start_time:.2f}s. Resultados: {len(results)}")
    return results[:limit]