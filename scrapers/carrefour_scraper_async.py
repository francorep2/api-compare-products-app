# scrapers/carrefour_scraper_async.py

import json
import re
from typing import Dict, Any, Optional
from urllib.parse import quote
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, TimeoutError
import asyncio

# --- HELPER DEFINITIVO PARA EXTRAER BARCODE DESDE __STATE__ ---

def _get_barcode_from_state(state_data: Dict, product_id: str) -> Optional[str]:
    """
    Navega la estructura del __STATE__ de Carrefour para encontrar el EAN.
    """
    if not state_data or not product_id:
        return None
        
    try:
        # 1. La clave del producto principal siempre sigue este patrón.
        product_key = f"Product:sp-{product_id}-none"
        product_obj = state_data.get(product_key)
        if not product_obj:
            return None

        # 2. La clave de los 'items' puede variar, la buscamos dinámicamente.
        items_key = next((k for k in product_obj if k.startswith("items(")), None)
        if not items_key:
            return None

        # 3. Dentro de 'items' está la referencia al objeto del SKU, la clave es 'id'.
        item_ref_id = product_obj[items_key][0].get("id")
        if not item_ref_id:
            return None

        # 4. Usamos esa referencia para encontrar el objeto del SKU en el __STATE__.
        item_data = state_data.get(item_ref_id)
        if not item_data:
            return None

        # 5. ¡Extraemos el EAN!
        barcode = item_data.get("ean")
        return barcode if isinstance(barcode, str) else None
        
    except (KeyError, IndexError, TypeError, StopIteration):
        # Si alguna de las claves no existe, devuelve None de forma segura.
        return None

# --- FUNCIÓN PRINCIPAL DE BÚSQUEDA ---

async def search(ctx: BrowserContext, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> Dict[str, Any]:
    search_term = barcode or q
    if not search_term:
        return {"source": "carrefour", "items": []}

    page = await ctx.new_page()
    items_list = []
    
    try:
        encoded_term = quote(search_term)
        search_url = f"https://www.carrefour.com.ar/{encoded_term}?_q={encoded_term}&map=ft"
        
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        try:
            await page.click('#onetrust-accept-btn-handler', timeout=7000)
            await asyncio.sleep(1)
        except TimeoutError:
            pass

        await page.wait_for_function("window.__STATE__ && Object.keys(window.__STATE__).length > 50", timeout=15000)
        gallery_selector = "div[class*='search-result-3-x-gallery']"
        await page.wait_for_selector(gallery_selector, timeout=20000)
        
        state_data = await page.evaluate("() => window.__STATE__")
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')

        product_elements = soup.select(f"{gallery_selector} section[class*='vtex-product-summary']")
        
        for product_el in product_elements[:limit]:
            # --- Título (con selectores de respaldo) ---
            title = None
            title_selectors = ["span[class*='productBrandName']", "h3[class*='productNameContainer']"]
            for sel in title_selectors:
                title_tag = product_el.select_one(sel)
                if title_tag and title_tag.text.strip():
                    title = title_tag.text.strip()
                    break
            
            price_tag = product_el.select_one("span[class*='sellingPriceValue']")
            price = price_tag.get_text(strip=True) if price_tag else None

            image_tag = product_el.find('img')
            image_url = image_tag['src'] if image_tag else None
            
            promo_tag = product_el.select_one("span[class*='tooltipText']")
            promotion = promo_tag.text.strip() if promo_tag else None
            
            link_tag = product_el.find('a')
            product_url = f"https://www.carrefour.com.ar{link_tag['href']}" if link_tag and link_tag.get('href') else None
            
            product_id = None
            if product_url:
                match = re.search(r'-(\d+)/p$', product_url)
                if match:
                    product_id = match.group(1)
            
            barcode_val = _get_barcode_from_state(state_data, product_id)

            items_list.append({
                'title': title, 'price': price, 'url': product_url,
                'image_url': image_url, 'barcode': barcode_val, 'promotion': promotion
            })
            
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")
        await page.screenshot(path="debug_carrefour_error.png", full_page=True)
    
    finally:
        await page.close()

    return { "source": "carrefour", "items": items_list }