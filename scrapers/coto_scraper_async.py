import asyncio
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote

import aiohttp
from bs4 import BeautifulSoup

# ===================== Configuración =====================

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = 200
TOP_N = 3 # Límite por defecto

# Regex para normalizar promociones (simplificado de tu versión)
PROMO_NORMALIZER = re.compile(
    r"\b(\d{1,2}\s*[xX×]\s*\d{1,2})\b|"          # 3x2
    r"\b(2da?\s+al\s+\d{1,3}\s*%)\b|"           # 2da al 70%
    r"\b(\d{1,3}\s*%?\s*(?:off|dto|descuento))\b", # 20% DTO
    re.IGNORECASE
)

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

    # Si no, usa el normalizador general
    match = PROMO_NORMALIZER.search(text)
    if match:
        # Encuentra el grupo que no es None
        promo_text = next((g for g in match.groups() if g is not None), None)
        if promo_text:
            return re.sub(r"\s+", " ", promo_text).upper().strip()
            
    return None

# ===================== Lógica Principal del Scraper =====================

async def coto_search_top_n(session: aiohttp.ClientSession, query: str, limit: int) -> List[Dict]:
    """
    Busca en Coto usando una única petición HTTP y parsea la grilla de resultados.
    Esta es la función principal y optimizada.
    """
    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    print(f"[LOG] Coto GET {search_url}")
    
    try:
        async with session.get(search_url, headers=headers, timeout=15) as response:
            response.raise_for_status()
            html = await response.text()
    except asyncio.TimeoutError:
        print(f"[ERROR] Coto timeout para la búsqueda: {query}")
        return []
    except aiohttp.ClientError as e:
        print(f"[ERROR] Coto error de red para la búsqueda: {query} | {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    
    product_items = soup.select('div#productsId div.item_product')
    if not product_items:
        print(f"[LOG] No se encontraron productos en la grilla para: {query}")
        return []

    results = []
    for item in product_items:
        if len(results) >= limit:
            break

        # Extraer toda la información de la tarjeta del producto
        link_tag = item.find('a')
        product_url = urljoin(COTO_BASE, link_tag.get('href', '')) if link_tag else None

        title_div = item.find('div', class_='descrip_full')
        title = title_div.text.strip() if title_div else "Título no encontrado"
        
        price_span = item.find('span', class_='atg_store_newPrice')
        price = normalize_price(price_span.text.strip() if price_span else None)

        image_tag = item.find('img', class_='zoomImage')
        image_url = image_tag.get('src') if image_tag else None
        
        promotion_div = item.find('div', class_='box_off')
        promotion = normalize_promotion(promotion_div.text.strip() if promotion_div else None)

        barcode_container = item.find('div', id=lambda x: x and x.startswith('producto_'))
        # El barcode aquí es en realidad el SKU de Coto, pero es un identificador útil.
        barcode = barcode_container['id'].split('_')[1] if barcode_container else None

        results.append({
            "title": title,
            "price": price,
            "url": product_url,
            "image_url": image_url,
            "barcode": barcode,
            "promotion": promotion,
        })
        
    return results

# ===================== API Pública del Scraper =====================

async def search(ctx: Dict, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    """
    Función de entrada del scraper. Utiliza una sesión aiohttp compartida si existe,
    o crea una nueva.
    """
    query = q.strip() if q and q.strip() else barcode
    if not query:
        return []

    # Reutiliza la sesión si el agregador la pasa en el contexto, si no, crea una.
    # Esto es una buena práctica para evitar crear conexiones TCP en cada llamada.
    session = ctx.get("aiohttp_session")
    
    if session:
        return await coto_search_top_n(session, query, limit)
    else:
        async with aiohttp.ClientSession() as new_session:
            return await coto_search_top_n(new_session, query, limit)
