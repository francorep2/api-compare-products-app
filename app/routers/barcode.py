# app/routers/barcode.py
from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Dict, List, Optional
import asyncio
import inspect
import logging
import re

from app.models.search_item import SearchItem
from app.services.coto import search_coto
from app.services.jumbo import search as search_jumbo
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
# Agregamos los nuevos resolvers
from scrapers.barcode_resolver import resolve_name_carrefour, resolve_name_dia, resolve_name_jumbo, resolve_name_coto
from app.dependencies.auth import get_current_user, User

router = APIRouter(prefix="/search/barcode", tags=["barcode"], dependencies=[Depends(get_current_user)])
log = logging.getLogger(__name__)

ALLOWED_SCRAPERS = {"carrefour", "coto", "jumbo", "dia"}
PLAYWRIGHT_SCRAPERS = {"coto", "jumbo"}

PLAYWRIGHT_CONCURRENCY_LIMIT = 1
playwright_semaphore = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY_LIMIT)

def _split_csv(values: Optional[List[str]]) -> List[str]:
    if not values: return []
    out: List[str] = []
    for v in values:
        if not v: continue
        out.extend([p.strip().lower() for p in v.split(",") if p.strip()])

    seen = set()
    norm = []
    for x in out:
        if x in ALLOWED_SCRAPERS and x not in seen:
            seen.add(x)
            norm.append(x)
    return norm

async def run_scraper_task(scraper_name: str, func, *args, **kwargs):
    is_playwright = scraper_name in PLAYWRIGHT_SCRAPERS

    if is_playwright:
        print(f"[Concurrency] Waiting for semaphore slot for Playwright scraper: {scraper_name}...")
        async with playwright_semaphore:
            print(f"[Concurrency] Acquired semaphore for Playwright scraper: {scraper_name}. Running...")
            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)
                print(f"[Concurrency] Finished Playwright scraper: {scraper_name}.")
                return result
            except Exception as e:
                 print(f"[Concurrency ERROR] Playwright scraper {scraper_name} failed: {e}")
                 return e
            finally:
                print(f"[Concurrency] Released semaphore for Playwright scraper: {scraper_name}.")
    else:
        print(f"Running non-Playwright task: {scraper_name}...")
        try:
             if inspect.iscoroutinefunction(func):
                 result = await func(*args, **kwargs)
             else:
                 result = await asyncio.to_thread(func, *args, **kwargs)
             print(f"Finished non-Playwright task: {scraper_name}.")
             return result
        except Exception as e:
             print(f"[Concurrency ERROR] Non-Playwright task {scraper_name} failed: {e}")
             return e

@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=8, max_length=14, regex=r"^\d+$", description="EAN-8, EAN-13, UPC-A, or similar barcode."),
    limit: int = Query(3, ge=1, le=5, description="Max results per supermarket."),
    enabled: Optional[List[str]] = Query(None, description="Comma-separated list or multiple params of scrapers to run (e.g., 'coto,jumbo' or enabled=coto&enabled=jumbo). Default: all."),
):
    enabled_set = set(_split_csv(enabled) or ALLOWED_SCRAPERS)
    print(f"Barcode search request: barcode={barcode}, limit={limit}, enabled={enabled_set}")

    initial_tasks = {}

    # Scrapers Playwright (buscan directo por barcode)
    if "coto" in enabled_set:
        initial_tasks["coto"] = run_scraper_task("coto", search_coto, barcode=barcode, limit=limit)
    if "jumbo" in enabled_set:
        initial_tasks["jumbo"] = run_scraper_task("jumbo", search_jumbo, barcode=barcode, limit=limit)

    # Resolvers (rápidos, no Playwright)
    if "carrefour" in enabled_set:
        initial_tasks["name_carrefour"] = run_scraper_task("resolve_carrefour", resolve_name_carrefour, barcode)
    if "dia" in enabled_set:
        initial_tasks["name_dia"] = run_scraper_task("resolve_dia", resolve_name_dia, barcode)
    # Agregamos resolvers para Coto y Jumbo
    if "coto" in enabled_set:
        initial_tasks["name_coto"] = run_scraper_task("resolve_coto", resolve_name_coto, barcode)
    if "jumbo" in enabled_set:
        initial_tasks["name_jumbo"] = run_scraper_task("resolve_jumbo", resolve_name_jumbo, barcode)


    print(f"Stage 1: Running {len(initial_tasks)} initial tasks...")
    initial_results = await asyncio.gather(*initial_tasks.values(), return_exceptions=True)
    initial_map = dict(zip(initial_tasks.keys(), initial_results))
    print("Stage 1: Gather complete.")

    coto_items: List[SearchItem] = []
    jumbo_items: List[SearchItem] = []

    def process_scraper_result(key: str) -> List[SearchItem]:
        if key not in initial_map: return []
        result = initial_map[key]
        if isinstance(result, Exception):
            log.error(f"Scraper task {key} failed: {result}")
            return []
        elif isinstance(result, list):
            valid_items = []
            for item_dict in result:
                try:
                    valid_items.append(SearchItem(**item_dict))
                except Exception as pydantic_error:
                    log.warning(f"Invalid item structure from {key}: {item_dict}. Error: {pydantic_error}")
            return valid_items
        else:
            log.warning(f"Unexpected result type for {key}: {type(result)}")
            return []

    coto_items = process_scraper_result("coto")
    jumbo_items = process_scraper_result("jumbo")

    def get_resolved_name(key: str) -> Optional[str]:
        result = initial_map.get(key)
        if isinstance(result, Exception):
            log.error(f"Resolver {key} failed: {result}")
            return None
        return result if isinstance(result, str) else None

    name_carrefour = get_resolved_name("name_carrefour")
    name_dia = get_resolved_name("name_dia")
    # Obtenemos los nombres resueltos para Coto y Jumbo
    name_coto = get_resolved_name("name_coto")
    name_jumbo = get_resolved_name("name_jumbo")
    print(f"Resolved names: CF='{name_carrefour}', Dia='{name_dia}', Coto='{name_coto}', Jumbo='{name_jumbo}'")

    text_tasks = {}

    # Scrapers que buscan por nombre resuelto
    if name_carrefour and "carrefour" in enabled_set:
         text_tasks["carrefour"] = run_scraper_task("carrefour", search_carrefour, q=name_carrefour, limit=limit)
    if name_dia and "dia" in enabled_set:
         text_tasks["dia"] = run_scraper_task("dia", search_dia, q=name_dia, limit=limit)
    # Nota: Coto y Jumbo ya buscaron por barcode en Stage 1.
    # Si quisieras *también* buscar por nombre en Coto/Jumbo si la búsqueda por barcode falló,
    # necesitarías agregar lógica aquí para verificar si coto_items/jumbo_items están vacíos
    # y si name_coto/name_jumbo existen, antes de lanzar la tarea.
    # Ejemplo (si quisieras buscar Jumbo por nombre si falló por barcode):
    # if not jumbo_items and name_jumbo and "jumbo" in enabled_set:
    #     text_tasks["jumbo_by_name"] = run_scraper_task("jumbo", search_jumbo, q=name_jumbo, limit=limit)


    text_map = {}
    if text_tasks:
        print(f"Stage 2: Running {len(text_tasks)} text search tasks...")
        text_results = await asyncio.gather(*text_tasks.values(), return_exceptions=True)
        text_map = dict(zip(text_tasks.keys(), text_results))
        print("Stage 2: Gather complete.")
    else:
        print("Stage 2: No text search tasks to run.")

    buckets: Dict[str, List[SearchItem]] = {}

    def process_text_search_result(key: str):
        if key not in text_map: return []
        result = text_map[key]
        if isinstance(result, Exception):
            log.error(f"Text search for {key} failed: {result}")
            return []
        elif isinstance(result, list):
            valid_items = []
            for item_dict in result:
                try:
                     valid_items.append(SearchItem(**item_dict))
                except Exception as pydantic_error:
                     log.warning(f"Invalid item structure from {key}: {item_dict}. Error: {pydantic_error}")
            return valid_items
        else:
            log.warning(f"Unexpected result type for {key}: {type(result)}")
            return []

    if "coto" in enabled_set: buckets["coto"] = coto_items
    if "jumbo" in enabled_set: buckets["jumbo"] = jumbo_items
    if "carrefour" in enabled_set: buckets["carrefour"] = process_text_search_result("carrefour")
    if "dia" in enabled_set: buckets["dia"] = process_text_search_result("dia")
    # Si agregaste jumbo_by_name en text_tasks, necesitas procesarlo aquí:
    # if "jumbo" in enabled_set and not buckets["jumbo"]: # Solo si la búsqueda directa falló
    #    buckets["jumbo"] = process_text_search_result("jumbo_by_name")


    results_enabled = {k: buckets.get(k, []) for k in enabled_set}
    total = sum(len(v) for v in results_enabled.values())

    print(f"Search complete. Total items found: {total}")

    return {
        "results": results_enabled,
        "count": total,
        "enabled": sorted(list(enabled_set)),
        "barcode": barcode,
        "limit": limit,
        "resolved_names": {
             "carrefour": name_carrefour,
             "dia": name_dia,
             "coto": name_coto,      # Agregado
             "jumbo": name_jumbo     # Agregado
        }
    }