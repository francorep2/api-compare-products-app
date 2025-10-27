# Reemplaza tu función compare_items_background con esta

import asyncio
import datetime
import uuid
from typing import List, Set, Optional, Dict
import logging # Para logging de errores

from app.schemas import shopping_list
from app.services import coto, dia, carrefour, jumbo # Asume que estos importan la función 'search' correcta
from app.services.text_matching import find_best_match_for_item
from app.core.browser_async import browser_manager_async
from app.task_db import tasks # Tu "almacén" de tareas

log = logging.getLogger(__name__)

SCRAPER_MODULES = { "coto": coto, "dia": dia, "carrefour": carrefour, "jumbo": jumbo }
ALLOWED_SCRAPERS = set(SCRAPER_MODULES.keys())
PLAYWRIGHT_SCRAPERS = {"coto", "jumbo"} # Marcar cuáles usan Playwright

# --- Limitador de Concurrencia para Playwright ---
# Es importante limitar cuántos scrapers pesados corren a la vez
PLAYWRIGHT_CONCURRENCY_LIMIT = 1
playwright_semaphore = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY_LIMIT)
# -------------------------------------------------

async def run_single_search(store_name: str, store_module, item_name: str, limit: int = 3):
    """ Ejecuta una única búsqueda para un item en una tienda, aplicando semáforo si es Playwright """
    is_playwright = store_name in PLAYWRIGHT_SCRAPERS
    
    if is_playwright:
        print(f"[{store_name}] Waiting for semaphore for item '{item_name}'...")
        async with playwright_semaphore:
            print(f"[{store_name}] Acquired semaphore for item '{item_name}'. Searching...")
            try:
                # La función 'search' del scraper ya maneja su propio timeout y contexto
                result = await store_module.search(q=item_name, limit=limit)
                print(f"[{store_name}] Finished search for item '{item_name}'.")
                return result
            except Exception as e:
                 log.error(f"[{store_name}] Scraper failed for item '{item_name}': {e}", exc_info=True)
                 return e # Devolver excepción para manejo posterior
            finally:
                print(f"[{store_name}] Released semaphore for item '{item_name}'.")
    else:
        # Scrapers sin Playwright (Dia, Carrefour)
        print(f"[{store_name}] Searching for item '{item_name}'...")
        try:
             result = await store_module.search(q=item_name, limit=limit)
             print(f"[{store_name}] Finished search for item '{item_name}'.")
             return result
        except Exception as e:
             log.error(f"[{store_name}] Scraper failed for item '{item_name}': {e}", exc_info=True)
             return e # Devolver excepción

async def compare_items_background(task_id: str, items: List[shopping_list.ComparisonItemIn], enabled_stores: Optional[List[str]] = None):
    """
    Función de fondo optimizada: procesa items secuencialmente por tienda
    y limita la concurrencia de scrapers Playwright.
    """
    tasks[task_id] = {"status": "processing", "data": None, "start_time": datetime.datetime.now().isoformat()}
    print(f"[BG Task] {task_id}: Iniciando comparación para {len(items)} productos...")
    
    enabled_set = set(enabled_stores) & ALLOWED_SCRAPERS if enabled_stores else ALLOWED_SCRAPERS
    
    all_stores_results: Dict[str, list] = {store: [] for store in enabled_set} # Inicializar listas

    # --- Ejecutar Búsquedas Secuencialmente por Tienda ---
    # Esto evita sobrecargar el servidor lanzando demasiadas tareas concurrentes,
    # especialmente útil para Playwright.
    for store_name in enabled_set:
        store_module = SCRAPER_MODULES.get(store_name)
        if not store_module:
            log.warning(f"[BG Task] {task_id}: Módulo no encontrado para {store_name}")
            all_stores_results[store_name] = [[] for _ in items] # Llenar con listas vacías
            continue
            
        print(f"[BG Task] {task_id}: Procesando tienda {store_name}...")
        store_item_results = []
        for item in items:
            # Ejecutar la búsqueda para este item y tienda
            result = await run_single_search(store_name, store_module, item.nombre)
            
            # Guardar el resultado (o lista vacía si falló)
            if isinstance(result, Exception):
                store_item_results.append([]) # Guardar lista vacía en caso de error
            elif isinstance(result, list):
                store_item_results.append(result)
            else:
                log.warning(f"Resultado inesperado de {store_name} para '{item.nombre}': {type(result)}")
                store_item_results.append([])
                
        all_stores_results[store_name] = store_item_results
        print(f"[BG Task] {task_id}: Tienda {store_name} procesada.")

    # --- Procesamiento de Resultados (como antes) ---
    print(f"[BG Task] {task_id}: Procesando resultados...")
    comparison_result = {
        store: {"total": 0.0, "items": [], "missing_count": 0, "missing_items": []} 
        for store in enabled_set
    }

    for i, item in enumerate(items):
        item_found_in_any_store = False # Para rastrear si el item se encontró en al menos una tienda
        for store_name in enabled_set:
            store_data = comparison_result[store_name] # Acceso directo al diccionario
            
            # Asegurarse de que el índice i existe en los resultados de la tienda
            if i < len(all_stores_results.get(store_name, [])):
                item_results_from_store = all_stores_results[store_name][i]
                
                # Pasar solo la lista de resultados (ya validada o [])
                best_match = find_best_match_for_item(item.nombre, item_results_from_store)
                
                if best_match and best_match.get("price"):
                    try:
                        # Extraer y limpiar precio (ej. quitar 'ARS ', convertir a float)
                        price_str = str(best_match["price"]).upper().replace("ARS", "").replace("$", "").replace(".", "").replace(",", ".").strip()
                        price = float(price_str)
                        
                        store_data["items"].append({
                            "original_name": item.nombre, 
                            "found_name": best_match.get("title"),
                            "price": price, 
                            "quantity": item.cantidad,
                            "url": best_match.get("url"), # Incluir URL si está disponible
                            "image_url": best_match.get("image_url") # Incluir imagen si está disponible
                        })
                        store_data["total"] += price * item.cantidad
                        item_found_in_any_store = True # Marcar como encontrado
                    except (ValueError, TypeError, AttributeError) as e:
                        log.warning(f"Error procesando precio '{best_match.get('price')}' para '{item.nombre}' en {store_name}: {e}")
                        store_data["missing_count"] += 1
                        store_data["missing_items"].append(item.nombre)
                else:
                    store_data["missing_count"] += 1
                    store_data["missing_items"].append(item.nombre)
            else:
                # Si no hay resultados para este índice (error previo), marcar como faltante
                store_data["missing_count"] += 1
                store_data["missing_items"].append(item.nombre)

    # --- Cálculo del Ganador y Totales (como antes) ---
    winner = min(
        (store for store, data in comparison_result.items() if data["missing_count"] == 0),
        key=lambda s: comparison_result[s]["total"], 
        default=None # Si ninguna tienda tiene todos los items, no hay ganador
    )
    
    totals_by_store = {
        store_name: {
            "total": round(data["total"], 2), # Redondear total
            "missing_count": data["missing_count"], 
            "missing_items": data["missing_items"]
        }
        for store_name, data in comparison_result.items()
    }
    
    final_data = {
        "totals_by_store": totals_by_store,
        "winner": winner,
        "comparison_details": comparison_result, # Incluir detalles completos opcionalmente
        "updated_at": datetime.datetime.now().isoformat()
    }
    
    # Guardar el resultado final
    tasks[task_id] = {"status": "completed", "data": final_data, "end_time": datetime.datetime.now().isoformat()}
    print(f"[BG Task] {task_id}: Comparación finalizada y guardada. Ganador: {winner}")