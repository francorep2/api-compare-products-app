import httpx
import asyncio
from typing import List, Dict, Optional

# --- Configuración ---

# El template que pediste, como un tipo de dato
Product = Dict[str, Optional[str]]

# URLs y Headers
# [CAMBIO] Esta es la nueva URL de la API v1 que usa Coto
COTO_API_URL = "https://www.cotodigital3.com.ar/api/v1/products/search"
COTO_BASE_URL = "https://www.cotodigital.com.ar"
SUCURSAL_ID = "200" # ID de sucursal que usabas antes

# Un User-Agent real para evitar bloqueos 403
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# Timeout agresivo. 8 segundos es más que suficiente para una API.
CLIENT_TIMEOUT = 8.0

# --- Scraper Principal ---

async def search_coto_by_api(query: str, limit: int = 3) -> List[Product]:
    """
    Busca productos en Coto usando su API interna v1.
    """
    
    # [CAMBIO] Los parámetros de la API v1 son diferentes
    params = {
        "query": query,     # 'q' ahora es 'query'
        "page": 1,
        "storeId": SUCURSAL_ID, # 'idSucursal' ahora es 'storeId'
        "sort": "relevance" # Nuevo parámetro requerido
    }
    
    results: List[Product] = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=CLIENT_TIMEOUT) as client:
            
            # 1. Hacer la petición a la nueva API
            response = await client.get(COTO_API_URL, params=params)
            response.raise_for_status() # Lanza error si es 4xx o 5xx
            
            data = response.json()
            
            # 2. Validar la respuesta (la estructura cambió)
            if not data or "products" not in data or not data["products"]:
                print(f"[LOG] Coto API v1: No se encontraron productos para '{query}'")
                return []
                
            raw_products = data["products"]
            
            # 3. Mapear los resultados a tu template
            for prod in raw_products:
                if len(results) >= limit:
                    break
                
                # --- [CAMBIO] Mapeo al nuevo template de la API v1 ---
                
                # a. title
                title = prod.get("name")
                
                # b. price (la API v1 lo da en un objeto 'price')
                price_obj = prod.get("price", {})
                price = price_obj.get("formattedValue") # Ej: "$ 1.499,00"

                # c. url (la API v1 da un 'slug' para armar la URL)
                prod_url = None
                if prod.get("slug"):
                    prod_url = f"{COTO_BASE_URL}/{prod['slug']}"

                # d. image_url (la API v1 da una lista de 'images')
                image_url = None
                images_list = prod.get("images", [])
                if images_list and isinstance(images_list, list) and images_list[0].get("url"):
                    image_url = images_list[0]["url"]
                
                # e. barcode (la API v1 lo llama 'ean')
                barcode = prod.get("ean")

                # -------------------------
                
                product_data: Product = {
                    "title": title,
                    "price": price,
                    "url": prod_url,
                    "image_url": image_url,
                    "barcode": barcode
                }
                results.append(product_data)

    except httpx.TimeoutException:
        print(f"[ERROR] Timeout: La API v1 de Coto no respondió a tiempo para '{query}'.")
        return []
        
    except httpx.HTTPStatusError as e:
        # El 404 que tenías antes debería aparecer aquí si la URL vuelve a cambiar
        print(f"[ERROR] HTTP Error v1: {e.response.status_code} al buscar '{query}'.")
        return []
        
    except Exception as e:
        print(f"[ERROR] Error inesperado v1 al procesar '{query}': {e}")
        return []
        
    return results


# --- Función de Búsqueda Principal (sin cambios) ---

async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Product]:
    """
    Función unificada que decide cómo buscar.
    """
    query_to_use = ""
    
    if barcode:
        query_to_use = barcode
    elif q:
        query_to_use = q
        
    if not query_to_use:
        return []

    print(f"[INFO] Buscando en API v1 de Coto: '{query_to_use}', limite={limit}")
    return await search_coto_by_api(query_to_use, limit=limit)