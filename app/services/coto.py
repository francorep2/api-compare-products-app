# app/services/coto.py
from __future__ import annotations
from typing import List, Dict, Optional
import asyncio
import logging

# ✅ IMPORTA EL SCRAPER SINCRÓNICO (requests) QUE TE PASÉ ANTES
from scrapers.coto_requests import (
    search_coto_requests as _search_coto_requests_sync,
    search_coto_by_barcode as _search_coto_by_barcode_sync,
)

log = logging.getLogger("coto")

# Nota: ya no necesitamos Playwright ni helpers de LD+JSON aquí.
# Todo lo hace el scraper síncrono y nosotros lo envolvemos async-safe.

async def search_coto(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3
) -> List[Dict]:
    """
    Wrapper async que ejecuta el scraper 'requests' en un thread.
    Respeta prioridad de barcode si viene, si no usa q.
    """
    if not q and not barcode:
        return []

    try:
        if barcode:
            log.info(f"[COTO] barcode={barcode} limit={limit}")
            return await asyncio.to_thread(_search_coto_by_barcode_sync, str(barcode), limit)
        else:
            log.info(f"[COTO] q={q} limit={limit}")
            return await asyncio.to_thread(_search_coto_requests_sync, q, limit)
    except Exception as e:
        log.exception(f"[COTO][ERROR] q={q} barcode={barcode} limit={limit} -> {type(e).__name__}: {e}")
        # En caso de error, devolvemos lista vacía para no romper el /search/all
        return []
