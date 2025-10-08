import asyncio
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote

from playwright.async_api import BrowserContext, Page, Locator, Error as PlaywrightError

# ===================== Configuración =====================

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = 200
TOP_N = 3 # Límite por defecto

# ===================== Funciones de Utilidad =====================

def normalize_price(text: Optional[str]) -> Optional[str]:
    """Extrae y formatea el precio de un texto."""
    if not text:
        return None
    # Limpia el texto, quita el símbolo de peso y normaliza espacios
    price_text = text.replace('$', '').strip()
    # Busca números que pueden tener puntos como separadores de miles y coma para decimales
    match = re.search(r'([\d\.,]+)', price_text)
    if not match:
        return None
    
    price_str = match.group(1).replace('.', '').replace(',', '.')
    try:
        price_float = float(price_str)
        return f"ARS {price_float:.2f}"
    except (ValueError, TypeError):
        return None

def normalize_promotion(text: Optional[str]) -> Optional[str]:
    """Extrae la mejor promoción de un texto y la estandariza."""
    if not text:
        return None
    
    # Busca un porcentaje de descuento explícito primero
    pct_match = re.search(r'\b(\d{1,3})\s*%', text)
    if pct_match:
        return f"{pct_match.group(1)}% DTO"
    
    # Regex simplificada para buscar promos comunes
    promo_normalizer = re.compile(
        r"\b(\d{1,2}\s*[xX×]\s*\d{1,2})\b|"
        r"\b(2da?\s+al\s+\d{1,3}\s*%)\b|"
        r"\b(\d{1,3}\s*%?\s*(?:off|dto|descuento))\b", re.IGNORECASE)
    
    match = promo_normalizer.search(text)
    if match:
        promo_text = next((g for g in match.groups() if g is not None), None)
        if promo_text:
            return re.sub(r"\s+", " ", promo_text).upper().strip()
            
    return None

# ===================== Lógica Principal del Scraper (Playwright Optimizado) =====================

async def parse_product_card(card: Locator) -> Dict:
    """Parsea una única tarjeta de producto de la grilla de resultados."""
    try:
        link_tag = card.locator('a').first
        href = await link_tag.get_attribute('href') or ''
        product_url = urljoin(COTO_BASE, href)

        title_div = card.locator('.descrip_full').first
        title = await title_div.inner_text() if await title_div.count() else "Título no encontrado"
        
        price_span = card.locator('.atg_store_newPrice').first
        price_text = await price_span.inner_text() if await price_span.count() else None
        price = normalize_price(price_text)

        image_tag = card.locator('img.zoomImage').first
        image_url = await image_tag.get_attribute('src') if await image_tag.count() else None
        
        promotion_div = card.locator('.box_off').first
        promo_text = await promotion_div.inner_text() if await promotion_div.count() else None
        promotion = normalize_promotion(promo_text)

        # El SKU de Coto es un buen fallback para un identificador único
        barcode_container = card.locator("xpath=./div[contains(@id, 'producto_')]").first
        barcode = None
        if await barcode_container.count():
            container_id = await barcode_container.get_attribute('id') or ''
            barcode = container_id.split('_')[-1]

        return {
            "title": title.strip(),
            "price": price,
            "url": product_url,
            "image_url": image_url,
            "barcode": barcode,
            "promotion": promotion,
        }
    except PlaywrightError as e:
        print(f"[ERROR] Error parseando una tarjeta de producto: {e}")
        return {}


async def coto_search_top_n(ctx: BrowserContext, query: str, limit: int) -> List[Dict]:
    """
    Busca en Coto usando Playwright para renderizar el JS de la grilla
    y luego parsea los resultados sin visitar cada página de producto.
    """
    page = await ctx.new_page()
    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )
    
    print(f"[LOG] Coto (Playwright) GET {search_url}")
    
    try:
        # Ir a la página y esperar a que la red esté tranquila, señal de que el JS cargó
        await page.goto(search_url, wait_until="networkidle", timeout=20000)
        
        # Espera explícita a que el contenedor de productos exista y sea visible
        grid_selector = 'div#productsId div.item_product'
        await page.wait_for_selector(grid_selector, state="visible", timeout=15000)
        
        product_cards = page.locator(grid_selector)
        count = await product_cards.count()
        
        if count == 0:
            print(f"[LOG] Grilla renderizada pero sin productos para: {query}")
            return []

        # Creamos tareas para parsear las tarjetas en paralelo
        tasks = []
        for i in range(min(count, limit)):
            card = product_cards.nth(i)
            tasks.append(parse_product_card(card))
        
        results_with_empty = await asyncio.gather(*tasks)
        # Filtramos posibles diccionarios vacíos por errores de parseo
        results = [r for r in results_with_empty if r]
        return results

    except PlaywrightError as e:
        print(f"[ERROR] Coto Playwright search falló para '{query}': {type(e).__name__} - {e}")
        try:
            # En caso de error, es útil guardar un screenshot para depurar
            path = f"debug_coto_error_{query.replace(' ','_')}.png"
            await page.screenshot(path=path, full_page=True)
            print(f"[LOG] Screenshot de error guardado en: {path}")
        except Exception as screenshot_error:
            print(f"[ERROR] No se pudo guardar el screenshot: {screenshot_error}")
        return []
    finally:
        await page.close()


# ===================== API Pública del Scraper =====================

async def search(ctx: BrowserContext, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    """
    Función de entrada del scraper. El `ctx` ahora es correctamente un `BrowserContext`.
    """
    query = q.strip() if q and q.strip() else barcode
    if not query:
        return []

    # Se asegura de que el contexto sea el esperado por la función de scraping
    if not isinstance(ctx, BrowserContext):
        print(f"[ERROR] Coto scraper recibió un contexto inesperado: {type(ctx).__name__}")
        return []

    return await coto_search_top_n(ctx, query, limit)

