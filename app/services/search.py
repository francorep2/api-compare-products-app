"""Búsqueda multi-tienda: todas las tiendas en paralelo."""
from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Optional

from app.providers import STORES, search_store

ALL_STORES = tuple(STORES)


def normalize_stores(enabled: Optional[Iterable[str]]) -> List[str]:
    """Acepta lista, claves repetidas o CSV ('coto,jumbo'); descarta desconocidas."""
    if not enabled:
        return list(ALL_STORES)
    out: List[str] = []
    for v in enabled:
        for part in str(v or "").replace("[]", "").split(","):
            s = part.strip().lower()
            if s in STORES and s not in out:
                out.append(s)
    return out or list(ALL_STORES)


async def search_all(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    stores: Optional[Iterable[str]] = None,
) -> Dict[str, List[Dict]]:
    names = normalize_stores(stores)
    results = await asyncio.gather(*(search_store(s, q=q, barcode=barcode, limit=limit) for s in names))
    return dict(zip(names, results))


async def search_by_barcode(
    barcode: str, limit: int = 3, stores: Optional[Iterable[str]] = None
) -> Dict[str, List[Dict]]:
    """EAN en todas las tiendas a la vez. A las que no lo tienen indexado les
    buscamos por el nombre del producto que encontró otra tienda."""
    names = normalize_stores(stores)
    out = await search_all(barcode=barcode, limit=limit, stores=names)

    missing = [s for s in names if not out[s]]
    if missing and len(missing) < len(names):
        title = next((out[s][0]["title"] for s in names if out[s]), None)
        if title:
            extra = await asyncio.gather(*(search_store(s, q=title, limit=limit) for s in missing))
            out.update(zip(missing, extra))
    return out
