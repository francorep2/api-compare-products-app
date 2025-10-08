import asyncio
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote

from playwright.async_api import BrowserContext, Page, Locator, Error as PlaywrightError

# ===================== Configuración =====================

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = 200
TOP_N = 3 # Límite por defecto

# ===================== Funciones de Utilidad (Sin cambios) =====================

def normalize_price(text: Optional[str]) -> Optional[str]:
    """Extrae y formatea el precio de un texto."""
    if not text: return None
    price_text = text.replace('$', '').strip()
    match = re.search(r'([\d\.,]+)', price_text)
    if not match: return None
    price_str = match.group(1).replace('.', '').replace(',', '.')
    try:
        price_float = float(price_str)
        return f"ARS {price_float:.2f}"
    except (ValueError, TypeError):
        return None

def normalize_promotion(text: Optional[str]) -> Optional[str]:
    """Extrae la mejor promoción de un texto y la estandariza."""
    if not text: return None
    pct_match = re.search(r'\b(\d{1,3})\s*%', text)
    if pct_match: return f"{pct_match.group(1)}% DTO"
    promo_normalizer = re.compile(
        r"\b(\d{1,2}\s*[xX×]\s*\d{1,2})\b|\b(2da?\s+al\s+\d{1,3}\s*%)\b|\b(\d{1,3}\s*%?\s*(?:off|dto|descuento))\b", re.IGNORECASE)
    match = promo_normalizer.search(text)
    if match:
        promo_text = next((g for g in match.groups() if g is not None), None)
        if promo_text: return re.sub(r"\s+", " ", promo_text).upper().strip()
    return None

# ===================== Lógica Principal del Scraper (CORREGIDA) =====================

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
    Busca en Coto usando Playwright, manejando pop-ups y con selectores actualizados.
    """
    page = await ctx.new_page()
    search_url = f"{COTO_BASE}/sitios/cdigi/categoria?Ntt={quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    
    print(f"[LOG] Coto (Playwright v3) GET {search_url}")
    
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        
        # --- NUEVO: Manejo de Pop-ups/Modales ---
        # Intenta hacer clic en cualquier botón de aceptación que pueda aparecer y bloquear la vista.
        try:
            accept_button = page.locator('button:text-matches("Aceptar|Entendido|Permitir|Permitir todas", "i")').first
            await accept_button.click(timeout=5000)
            print("[LOG] Pop-up de consentimiento cerrado.")
            # Espera un poco para que la UI se estabilice tras cerrar el modal
            await page.wait_for_timeout(1000)
        except PlaywrightError:
            # Si no encuentra el botón, no es un error, simplemente no había pop-up.
            print("[LOG] No se encontró pop-up de consentimiento.")
            pass
            
        # --- SELECTOR CORREGIDO ---
        # La grilla ahora es un UL con clase 'products' y los items son LI.
        grid_selector = 'ul.products > li.clearfix'
        await page.wait_for_selector(grid_selector, state="visible", timeout=15000)
        
        product_cards = page.locator(grid_selector)
        count = await product_cards.count()
        
        if count == 0:
            print(f"[LOG] Grilla renderizada pero sin productos para: {query}")
            return []
        
        print(f"[LOG] Se encontraron {count} productos en la grilla.")

        tasks = [parse_product_card(product_cards.nth(i)) for i in range(min(count, limit))]
        results_with_empty = await asyncio.gather(*tasks)
        results = [r for r in results_with_empty if r]
        return results

    except PlaywrightError as e:
        print(f"[ERROR] Coto Playwright search falló para '{query}': {type(e).__name__} - {e}")
        try:
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
    Función de entrada del scraper.
    """
    query = q.strip() if q and q.strip() else barcode
    if not query:
        return []

    if not isinstance(ctx, BrowserContext):
        print(f"[ERROR] Coto scraper recibió un contexto inesperado: {type(ctx).__name__}")
        return []

    return await coto_search_top_n(ctx, query, limit)

