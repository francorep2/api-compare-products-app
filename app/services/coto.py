# app/app/services/coto.py
from __future__ import annotations
from typing import Optional, List, Dict
import asyncio

# Importá el scraper nuevo (requests + bs4)
# Ajustá el path si tu paquete se llama distinto.
from scrapers.coto_scraper_async import (
    search as _search_sync,
    search_top_n as _search_top_n_sync,
    coto_search_by_barcode as _search_by_barcode_sync,
)

# Mantiene la firma antigua con ctx para no tocar routers/llamadas
async def coto_search(
    ctx=None,
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
) -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _search_sync(q=q, barcode=barcode, limit=limit)
    )

async def coto_search_top_n(
    ctx=None,
    query: str = "",
    limit: int = 3,
) -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _search_top_n_sync(query, limit=limit)
    )

async def coto_search_by_barcode(
    ctx=None,
    ean: str = "",
    limit: int = 3,
) -> List[Dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _search_by_barcode_sync(ean, limit=limit)
    )
