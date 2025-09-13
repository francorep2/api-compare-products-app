# scrapers/jumbo_scraper_barcode.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Dict, Optional

from scrapers.barcode_resolver import resolve_name_via_store
# IMPORTA TU SCRAPER "POR NOMBRE" QUE YA FUNCIONA (NO LO MODIFICAMOS)
from scrapers.jumbo_scraper_async import search as jumbo_search_by_name

BASE = "https://www.jumbo.com.ar"

async def search(ctx, barcode: str, limit: int = 3) -> List[Dict]:
    name: Optional[str] = await resolve_name_via_store(ctx, BASE, barcode)
    if not name:
        return []
    return await jumbo_search_by_name(ctx, q=name, barcode=None, limit=limit)
