import httpx
import asyncio
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re
import unicodedata

# --- Configuración ---

# El template que pediste
Product = Dict[str, Optional[str]]

COTO_BASE_URL = "https://www.cotodigital3.com.ar"

# --- MAPA DE CATEGORÍAS ---
# Aquí mapeamos palabras clave a las URLs de categorías estáticas
# Tienes que agregar más URLs de l.asp aquí para más productos
CATEGORY_MAP = {
    "gaseosa": "https://www.cotodigital3.com.ar/l.asp?cat=775&id=775",
    "pepsi": "https://www.cotodigital3.com.ar/l.asp?cat=775&id=775",
    "coca": "https://www.cotodigital3.com.ar/l.asp?cat=775&id=775",
    "leche": "https://www.cotodigital3.com.ar/l.asp?cat=753&id=753",
    "yogur": "https://www.cotodigital3.com.ar/l.asp?cat=755&id=755",
    "arroz": "https://www.cotodigital3.com.ar/l.asp?cat=61&id=61",
    # Agrega más...
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
}

CLIENT_TIMEOUT = 8.0 # Timeout de 8s (más que suficiente)

# --- Funciones de Parseo (Helpers) ---

def _normalize_text(s: str) -> str:
    """Quita tildes y pasa a minúscula para comparar."""
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s

def _normalize_price(price_str: str) -> Optional[str]:
    """Limpia el string de precio y lo formatea."""
    try:
        cleaned = price_str.replace("$", "").replace(".", "").replace(",", ".").strip()
        price_float = float(cleaned)
        return f"ARS {price_float:.2f}"
    except Exception:
        return None


def _get_products_from_html(content: str) -> List[Product]:
    """
    Parsea el HTML estático (de l.asp) y extrae los productos.
    """
    soup = BeautifulSoup(content, "html5lib")
    results: List[Product] = []

    list_products = soup.find("ul", id="products")
    if not list_products:
        return []

    products = list_products.find_all("li", id=re.compile(r"^li_prod"))
    
    for li in products:
        try:
            prod_id_raw = li.get("id", "").replace("li_prod", "")
            
            title_element = li.find("div", id=f"descrip_full_sku{prod_id_raw}")
            if not title_element: continue

            title = title_element.get_text(strip=True)
            prod_url = COTO_BASE_URL + (li.a.get('href') if li.a else '')

            price_span = li.find("span", class_="atg_store_newPrice")
            price = None
            if price_span:
                if price_span.span: price_span.span.decompose()
                if price_span.br: price_span.br.decompose()
                price_text = price_span.get_text(strip=True)
                price = _normalize_price(price_text)

            ean_div = li.find("div", {"data-ean": True})
            barcode = ean_div['data-ean'] if ean_div else None
            
            image_element = li.find("img", id=re.compile(r"^imagen_"))
            image_url = image_element.get("src") if image_element else None
            
            product_data: Product = {
                "title": title,
                "price": price,
                "url": prod_url,
                "image_url": image_url,
                "barcode": barcode
            }
            results.append(product_data)
        
        except Exception:
            continue
            
    return results

# --- Funciones de Scrapeo (Async) ---

# Variable global para caché simple (opcional pero recomendado)
# Esto guarda los resultados de una categoría por 10 minutos
g_cache = {}
g_cache_expiry = {}

async def scrape_category(category_url: str) -> List[Product]:
    """
    Scraper rápido que obtiene TODOS los productos de una URL de categoría.
    Usa una caché simple en memoria.
    """
    global g_cache, g_cache_expiry
    
    # 1. Revisar caché
    now = asyncio.get_event_loop().time()
    if category_url in g_cache and now < g_cache_expiry.get(category_url, 0):
        print(f"[LOG] Usando caché para {category_url}")
        return g_cache[category_url]

    # 2. Si no hay caché, scrapear
    print(f"[LOG] Scrapeando URL: {category_url}")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=CLIENT_TIMEOUT, follow_redirects=True) as client:
            
            response = await client.get(category_url)
            response.raise_for_status()
            html_content = response.text
            
            product_list = await asyncio.to_thread(_get_products_from_html, html_content)
            
            # 3. Guardar en caché
            g_cache[category_url] = product_list
            g_cache_expiry[category_url] = now + 600 # Expira en 10 minutos
            
            return product_list

    except Exception as e:
        print(f"[ERROR] Error en scrape_category: {e}")
        return []


# --- ¡NUEVA FUNCIÓN DE BÚSQUEDA! ---

async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Product]:
    """
    Busca un producto filtrando una categoría predefinida.
    """
    if not q:
        return [] # No podemos hacer nada sin un query 'q'

    norm_q = _normalize_text(q)
    target_url = None

    # 1. Encontrar la categoría basada en la palabra clave
    for keyword, url in CATEGORY_MAP.items():
        if keyword in norm_q:
            target_url = url
            break
            
    if not target_url:
        print(f"[LOG] No se encontró categoría mapeada para '{q}'")
        return [] # No sabemos qué categoría scrapear

    # 2. Traer TODOS los productos de esa categoría (usará caché si puede)
    all_products = await scrape_category(target_url)

    # 3. Filtrar la lista en Python
    filtered_products: List[Product] = []
    
    # Preparamos las palabras clave de la búsqueda
    query_words = set(norm_q.split())

    for prod in all_products:
        prod_title = prod.get("title")
        if not prod_title:
            continue
            
        norm_title = _normalize_text(prod_title)
        
        # Revisamos si todas las palabras de la búsqueda están en el título
        if all(word in norm_title for word in query_words):
            filtered_products.append(prod)
    
    # 4. Devolver solo el límite pedido
    return filtered_products[:limit]

