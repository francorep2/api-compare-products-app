import httpx
import asyncio
from typing import List, Dict, Optional

# --- Configuración ---

# El template que pediste, como un tipo de dato
Product = Dict[str, Optional[str]]

# URLs y Headers
# Usamos la API de Coto, no la web
COTO_API_URL = "https://www.cotodigital3.com.ar/api/search"
COTO_BASE_URL = "https://www.cotodigital.com.ar"
SUCURSAL_ID = "200" # ID de sucursal que usabas antes

# Un User-Agent real para evitar bloqueos 403
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# Timeout agresivo. 8 segundos es más que suficiente para una API.
# Esto previene el timeout de 10-12s de Koyeb.
CLIENT_TIMEOUT = 8.0

# --- Scraper Principal ---

async def search_coto_by_api(query: str, limit: int = 3) -> List[Product]:
    """
    Busca productos en Coto usando su API interna.
    Es rápido y cumple con el template requerido.
    """
    
    # Parámetros para la API
    params = {
        "q": query,
        "page": 1,
        "idSucursal": SUCURSAL_ID
    }
    
    results: List[Product] = []

    try:
        # Usamos un cliente async
        async with httpx.AsyncClient(headers=HEADERS, timeout=CLIENT_TIMEOUT) as client:
            
            # 1. Hacer la petición a la API
            response = await client.get(COTO_API_URL, params=params)
            
            # Levantar un error si Coto nos bloquea (4xx) o falla (5xx)
            response.raise_for_status() 
            
            data = response.json()
            
            # 2. Validar la respuesta
            if not data or "products" not in data or not data["products"]:
                print(f"[LOG] Coto API: No se encontraron productos para '{query}'")
                return []
                
            raw_products = data["products"]
            
            # 3. Mapear los resultados a tu template
            for prod in raw_products:
                if len(results) >= limit:
                    break # Salir si ya tenemos los productos que queríamos
                
                # --- Mapeo al template ---
                
                # a. title
                title = prod.get("name")
                
                # b. price (la API ya lo da formateado como string)
                price = prod.get("price")
                if isinstance(price, (int, float)):
                    price = f"ARS {price:.2f}" # Si viniera como nro, lo formateamos

                # c. url (la API da el 'link' relativo)
                prod_url = None
                if prod.get("link"):
                    prod_url = f"{COTO_BASE_URL}{prod['link']}"

                # d. image_url
                image_url = prod.get("mainImage")
                
                # e. barcode (la API lo llama 'ean')
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
        # Esto pasa si la API de Coto tarda más que CLIENT_TIMEOUT
        print(f"[ERROR] Timeout: La API de Coto no respondió a tiempo para '{query}'.")
        return [] # Devolvemos lista vacía (falla rápido)
        
    except httpx.HTTPStatusError as e:
        # Esto pasa si Coto devuelve 403 (Bloqueado), 404, 500 (Error de Coto), etc.
        print(f"[ERROR] HTTP Error: {e.response.status_code} al buscar '{query}'.")
        return []
        
    except Exception as e:
        # Cualquier otro error (ej. JSON mal formado)
        print(f"[ERROR] Error inesperado al procesar '{query}': {e}")
        return []
        
    return results


# --- Función de Búsqueda Principal (para tu endpoint) ---

async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Product]:
    """
    Función unificada que decide cómo buscar.
    """
    query_to_use = ""
    
    # Priorizamos el barcode si existe
    if barcode:
        query_to_use = barcode
    elif q:
        query_to_use = q
        
    if not query_to_use:
        return [] # No hay nada que buscar

    print(f"[INFO] Buscando en API de Coto: '{query_to_use}', limite={limit}")
    return await search_coto_by_api(query_to_use, limit=limit)


# --- Ejemplo de cómo usarlo (para probar localmente) ---

