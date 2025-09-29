import asyncio
import datetime
import uuid
from typing import List, Set, Optional, Dict

from app.schemas import shopping_list
from app.services import coto, dia, carrefour, jumbo
from app.services.text_matching import find_best_match_for_item
from app.core.browser_async import browser_manager_async
from app.task_db import tasks 

SCRAPER_MODULES = { "coto": coto, "dia": dia, "carrefour": carrefour, "jumbo": jumbo }

async def compare_items_background(task_id: str, items: List[shopping_list.ComparisonItemIn], enabled_stores: Optional[List[str]] = None):
    """
    Esta función se ejecuta en segundo plano. No devuelve nada, sino que
    actualiza el diccionario 'tasks' con el resultado final.
    """
    # Marcamos la tarea como "en proceso"
    tasks[task_id] = {"status": "processing", "data": None}
    print(f"[BG Task] {task_id}: Iniciando comparación para {len(items)} productos...")
    
    allowed_stores = set(SCRAPER_MODULES.keys())
    enabled_set = set(enabled_stores) & allowed_stores if enabled_stores else allowed_stores

    tasks_map: Dict[str, asyncio.Task] = {}
    
    async def coto_worker():
        async with browser_manager_async.new_context() as coto_ctx:
            coto_tasks = [coto.search(ctx=coto_ctx, q=item.nombre, limit=3) for item in items]
            return await asyncio.gather(*coto_tasks, return_exceptions=True)

    if "coto" in enabled_set:
        tasks_map["coto"] = asyncio.create_task(coto_worker())
    
    for store_name in ["dia", "carrefour", "jumbo"]:
        if store_name in enabled_set:
            store_module = SCRAPER_MODULES.get(store_name)
            if store_module:
                async def api_store_worker(module=store_module):
                    api_tasks = [module.search(q=item.nombre, limit=3) for item in items]
                    return await asyncio.gather(*api_tasks, return_exceptions=True)
                tasks_map[store_name] = asyncio.create_task(api_store_worker())

    await asyncio.wait(tasks_map.values())

    all_stores_results: Dict[str, list] = {}
    for store_name, task in tasks_map.items():
        try:
            result = task.result()
            all_stores_results[store_name] = [res if not isinstance(res, Exception) else [] for res in result]
        except Exception as e:
            print(f"[BG Task] {task_id}: Error en el worker de la tienda {store_name}: {e}")
            all_stores_results[store_name] = [[] for _ in items]

    comparison_result = {
        store: {"total": 0.0, "items": [], "missing_count": 0, "missing_items": []} 
        for store in enabled_set
    }

    for i, item in enumerate(items):
        for store_name in enabled_set:
            if store_name in all_stores_results and i < len(all_stores_results[store_name]):
                item_results_from_store = all_stores_results[store_name][i]
                best_match = find_best_match_for_item(item.nombre, item_results_from_store)
                
                if best_match and best_match.get("price"):
                    try:
                        price = float(str(best_match["price"]).replace("ARS", "").strip())
                        comparison_result[store_name]["items"].append({
                            "original_name": item.nombre, "found_name": best_match.get("title"),
                            "price": price, "quantity": item.cantidad,
                        })
                        comparison_result[store_name]["total"] += price * item.cantidad
                    except (ValueError, AttributeError):
                        comparison_result[store_name]["missing_count"] += 1
                        comparison_result[store_name]["missing_items"].append(item.nombre)
                else:
                    comparison_result[store_name]["missing_count"] += 1
                    comparison_result[store_name]["missing_items"].append(item.nombre)
            else:
                 comparison_result[store_name]["missing_count"] += 1
                 comparison_result[store_name]["missing_items"].append(item.nombre)

    winner = min((store for store, data in comparison_result.items() if data["missing_count"] == 0),
        key=lambda s: comparison_result[s]["total"], default=None)
    
    totals_by_store = {
        store_name: {"total": data["total"], "missing_count": data["missing_count"], "missing_items": data["missing_items"]}
        for store_name, data in comparison_result.items()
    }
    
    final_data = {
        "totals_by_store": totals_by_store,
        "winner": winner,
        "updated_at": datetime.datetime.now().isoformat()
    }
    
    # Guardamos el resultado final en el "almacén"
    tasks[task_id] = {"status": "completed", "data": final_data}
    print(f"[BG Task] {task_id}: Comparación finalizada y guardada.")