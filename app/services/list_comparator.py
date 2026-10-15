"""Comparación de una lista de compras entre supermercados.

Todas las búsquedas (ítems x tiendas) salen en paralelo; el cache evita repetir consultas.

Resultado guardado (todo aditivo respecto de la versión anterior):
  totals_by_store: {tienda: {total, missing_count, missing_items}}
  winner:          tienda completa más barata (o None si ninguna tiene todo)
  savings:         {amount, pct, vs_store}  ahorro del ganador vs la tienda completa más cara
  lines:           una por producto: {name, quantity, offers: {tienda: oferta|None}, cheapest}
  optimal:         "mejor combinación": cada producto en la tienda donde sale más barato
  items_count, stores, updated_at
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
from app import task_db

log = logging.getLogger("comparator")

MAX_PARALLEL = 32  # búsquedas simultáneas por comparación


def _price(item: Dict) -> Optional[float]:
    try:
        return float(str(item.get("price")).replace("ARS", "").strip())
    except (TypeError, ValueError):
        return None


async def _save(task_id: str, data: Dict) -> None:
    # SQLAlchemy es síncrono: lo sacamos del event loop
    await asyncio.to_thread(task_db.finish_task, task_id, data)


def _offer(match: Optional[Dict]) -> Optional[Dict]:
    price = _price(match) if match else None
    if match is None or price is None:
        return None
    return {
        "found_name": match.get("title"),
        "price": price,
        "url": match.get("url"),
        "image_url": match.get("image_url"),
        "promotion": match.get("promotion"),
        "barcode": match.get("barcode"),
    }


def build_result(items: List[shopping_list.ComparisonItemIn], stores: List[str], matches: List[List[Optional[Dict]]]) -> Dict:
    """Arma el resultado a partir de `matches[i][j]` = mejor producto del ítem i en la tienda j."""
    summary = {s: {"total": 0.0, "missing_count": 0, "missing_items": []} for s in stores}
    lines: List[Dict] = []
    opt_items: List[Dict] = []
    opt_missing: List[str] = []

    for it, row in zip(items, matches):
        offers = {s: _offer(m) for s, m in zip(stores, row)}
        for s, o in offers.items():
            if o is None:
                summary[s]["missing_count"] += 1
                summary[s]["missing_items"].append(it.nombre)
            else:
                summary[s]["total"] += o["price"] * it.cantidad
        found = {s: o for s, o in offers.items() if o}
        cheapest = min(found, key=lambda s: found[s]["price"]) if found else None
        lines.append({"name": it.nombre, "quantity": it.cantidad, "barcode": it.barcode, "offers": offers, "cheapest": cheapest})
        if cheapest:
            o = found[cheapest]
            opt_items.append({"name": it.nombre, "quantity": it.cantidad, "store": cheapest, **o,
                              "subtotal": round(o["price"] * it.cantidad, 2)})
        else:
            opt_missing.append(it.nombre)

    for v in summary.values():
        v["total"] = round(v["total"], 2)

    complete = [s for s in stores if summary[s]["missing_count"] == 0]
    winner = min(complete, key=lambda s: summary[s]["total"], default=None)

    savings = None
    if winner and len(complete) > 1:
        worst = max(complete, key=lambda s: summary[s]["total"])
        amount = round(summary[worst]["total"] - summary[winner]["total"], 2)
        if amount > 0:
            savings = {"amount": amount, "pct": round(amount / summary[worst]["total"] * 100, 1), "vs_store": worst}

    opt_total = round(sum(i["subtotal"] for i in opt_items), 2)
    optimal = {
        "total": opt_total,
        "items": opt_items,
        "missing_items": opt_missing,
        "stores_used": sorted({i["store"] for i in opt_items}),
        "savings_vs_winner": round(summary[winner]["total"] - opt_total, 2) if winner and not opt_missing else None,
    }
    return {
        "totals_by_store": summary,
        "winner": winner,
        "savings": savings,
        "lines": lines,
        "optimal": optimal,
        "items_count": len(items),
        "stores": stores,
        "updated_at": datetime.datetime.now().isoformat(),
    }


async def compare_items_background(
    task_id: str,
    items: List[shopping_list.ComparisonItemIn],
    enabled_stores: Optional[List[str]] = None,
):
    """Corre en segundo plano y guarda el resultado en la tabla comparison_tasks."""
    started = time.monotonic()

    stores = [s for s in (enabled_stores or STORES) if s in STORES]
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def _one(store: str, it: shopping_list.ComparisonItemIn) -> Optional[Dict]:
        async with sem:
            found: List[Dict] = []
            if it.barcode:
                # producto exacto: EAN directo en la tienda; si no lo indexa, caemos al nombre
                found = await search_store(store, barcode=it.barcode, limit=3)
                if found:
                    return found[0]
            found = await search_store(store, q=it.nombre, limit=6)
            return find_best_match_for_item(it.nombre, found)

    try:
        matches = await asyncio.gather(*[
            asyncio.gather(*[_one(store, it) for store in stores]) for it in items
        ])
        await _save(task_id, build_result(items, stores, [list(r) for r in matches]))
        log.info("comparación %s lista en %.1fs (%d ítems x %d tiendas)",
                 task_id, time.monotonic() - started, len(items), len(stores))
    except Exception:  # noqa: BLE001
        log.exception("comparación %s falló", task_id)
        await _save(task_id, {
            "totals_by_store": {},
            "winner": None,
            "savings": None,
            "lines": [],
            "optimal": None,
            "items_count": len(items),
            "stores": stores,
            "error": True,
            "updated_at": datetime.datetime.now().isoformat(),
        })
