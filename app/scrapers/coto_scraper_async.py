# scrapers_async/coto_scraper_async.py
from typing import List, Dict, Optional
import json, urllib.parse, re, time, datetime, os
import asyncio
import re

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = text.replace("\xa0", " ").strip()
    # Expresión regular mejorada para capturar precios de Coto
    m = re.search(r'\$\s*([\d.,]+)', t)
    if not m: return None
    price_str = m.group(1).replace(".", "").replace(",", ".")
    try:
        price_float = float(price_str)
        return f"ARS {price_float:.2f}"
    except:
        return None

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

def absolutize(base: str, href: str) -> str:
    if not href or href.startswith("http"): return href
    return urllib.parse.urljoin(base, href)

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", s or "")
    return d if 8 <= len(d) <= 14 else None

# --- SCRAPER PRINCIPAL DE COTO ---

COTO_BASE = "https://www.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200
TOP_N = 3

async def coto_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    search_url = f"{COTO_BASE}/sitios/cdigi/lecat/lecat.jsp? قاعده N=1010026&Ntt={urllib.parse.quote(query)}"
    page = await ctx.new_page()
    try:
        print(f"[LOG] Coto GET {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=50000)

        # Selector actualizado para la grilla de productos de Coto
        product_list_selector = "#products-gallery-fs ul.clearfix > li"
        await page.wait_for_selector(product_list_selector, timeout=15000)
        
        products = await page.query_selector_all(product_list_selector)
        print(f"[LOG] Productos encontrados en Coto: {len(products)}")

        results = []
        for p_element in products[:limit*2]:
            if len(results) >= limit: break
            
            # Selectores actualizados para los datos dentro de cada producto
            title_el = await p_element.query_selector(".descrip_full")
            price_el = await p_element.query_selector(".price_regular_precio")
            link_el = await p_element.query_selector("a")
            img_el = await p_element.query_selector("img")

            if not all([title_el, price_el, link_el, img_el]): continue

            title = clean_text(await title_el.inner_text())
            price = normalize_price(await price_el.inner_text())
            url = absolutize(COTO_BASE, await link_el.get_attribute("href") or "")
            image_url = absolutize(COTO_BASE, await img_el.get_attribute("src") or "")

            results.append({
                "title": title,
                "price": price,
                "url": url,
                "image_url": image_url,
                "barcode": None # El EAN en Coto requiere ir al PDP, se omite por velocidad
            })

        return results[:limit]
    except Exception as e:
        print(f"[LOG] Coto search error: {e}")
        await save_debug_html(page, "coto_search_error")
        return []
    finally:
        await page.close()

async def coto_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    return await coto_search_top_n(ctx, ean, limit=limit)

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        return await coto_search_by_barcode(ctx, ean, limit=limit)
    if q and q.strip():
        return await coto_search_top_n(ctx, q.strip(), limit=limit)
    return []