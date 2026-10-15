"""Comparación de una lista de compras entre supermercados.

Todas las búsquedas (ítems x tiendas) salen en paralelo; el cache evita repetir
consultas y las tiendas ya no necesitan navegador, así que una lista de ~20
productos se resuelve en pocos segundos.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Dict, List, Optional

from app.providers import STORES, search_store
from app.schemas import shopping_list
from app.services.text_matching import find_best_match_for_item
from app.task_db import tasks

log = logging.getLogger("comparator")

TASK_TTL_SEC = 60 * 60
MAX_PARALLEL = 32  # búsquedas simultáneas por comparación


def _prune_tasks() -> None:
    now = time.time()
    for tid in [t for t, v in tasks.items() if now - v.get("created", now) > TASK_TTL_SEC]:
        tasks.pop(tid, None)


def _price(item: Dict) -> Optional[float]:
    try:
        return float(str(item.get("price")).replace("ARS", "").strip())
    except (TypeError, ValueError):
        return None


def _result(data: Dict) -> Dict:
    return {"status": "completed", "created": time.time(), "data": data}


async def compare_items_background(
    task_id: str,
    items: List[shopping_list.ComparisonItemIn],
    enabled_stores: Optional[List[str]] = None,
):
    """Corre en segundo plano y deja el resultado en `tasks[task_id]`."""
    _prune_tasks()
    tasks[task_id] = {"status": "processing", "data": None, "created": time.time()}
    started = time.monotonic()

    stores = [s for s in (enabled_stores or STORES) if s in STORES]
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def _one(store: str, name: str) -> List[Dict]:
        async with sem:
            return await search_store(store, q=name, limit=6)

    try:
        grid = await asyncio.gather(*[
            asyncio.gather(*[_one(store, it.nombre) for store in stores]) for it in items
        ])

        summary = {s: {"total": 0.0, "missing_count": 0, "missing_items": []} for s in stores}
        for it, per_store in zip(items, grid):
            for store, found in zip(stores, per_store):
                best = find_best_match_for_item(it.nombre, found)
                price = _price(best) if best else None
                if price is None:
                    summary[store]["missing_count"] += 1
                    summary[store]["missing_items"].append(it.nombre)
                else:
                    summary[store]["total"] += price * it.cantidad

        complete = [s for s in stores if summary[s]["missing_count"] == 0]
        winner = min(complete, key=lambda s: summary[s]["total"], default=None)
        tasks[task_id] = _result({
            "totals_by_store": summary,
            "winner": winner,
            "updated_at": datetime.datetime.now().isoformat(),
        })
        log.info("comparación %s lista en %.1fs (%d ítems x %d tiendas)",
                 task_id, time.monotonic() - started, len(items), len(stores))
    except Exception:  # noqa: BLE001
        log.exception("comparación %s falló", task_id)
        tasks[task_id] = _result({
            "totals_by_store": {},
            "winner": None,
            "updated_at": datetime.datetime.now().isoformat(),
        })
