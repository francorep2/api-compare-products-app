# app/services/all_async.py
from typing import Dict, List, Optional
import asyncio
from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo
import asyncio
from typing import List, Dict, Optional

async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3
) -> Dict[str, List[Dict]]:
    
    # --- 1. Separamos los scrapers por tipo ---
    light_providers = {
        "coto": search_coto,
        "jumbo": search_jumbo,
    }
    
    heavy_providers = {
        "carrefour": search_carrefour,
        "dia": search_dia,
    }

    # --- 2. Creamos un "semáforo" que solo deja pasar a un scraper pesado a la vez ---
    browser_semaphore = asyncio.Semaphore(1)

    async def run_heavy_task(fn, name):
        async with browser_semaphore:
            print(f"-> Iniciando scraper pesado: {name}")
            result = await fn(q=q, barcode=barcode, limit=limit)
            print(f"<- Finalizado scraper pesado: {name}")
            return result

    # --- 3. Creamos las tareas ---
    tasks = {}
    # Tareas ligeras (corren todas juntas)
    for name, fn in light_providers.items():
        tasks[name] = asyncio.create_task(fn(q=q, barcode=barcode, limit=limit))
    
    # Tareas pesadas (el semáforo las ordena para que corran de a una)
    for name, fn in heavy_providers.items():
        tasks[name] = asyncio.create_task(run_heavy_task(fn, name))

    # --- 4. Recolectamos los resultados ---
    results: Dict[str, List[Dict]] = {}
    for name, task in tasks.items():
        try:
            # La estructura de tu scraper de Carrefour devuelve un dict, lo ajustamos
            response_data = await task
            items = response_data.get("items") if isinstance(response_data, dict) else response_data
            
            # Limpiamos la salida como ya hacías
            clean_items = [
                {
                    "title": it.get("title"), "price": it.get("price"), "url": it.get("url"),
                    "image_url": it.get("image_url"), "barcode": it.get("barcode"), "promotion": it.get("promotion"),
                }
                for it in items if it
            ]
            results[name] = clean_items
        except Exception as e:
            print(f"Error al procesar el resultado de {name}: {e}")
            results[name] = []
            
    return results