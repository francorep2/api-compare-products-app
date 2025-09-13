# scrapers/carrefour_scraper_barcode.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Dict, Optional

from scrapers.barcode_resolver import resolve_name_via_store
# IMPORTA TU SCRAPER "POR NOMBRE" QUE YA FUNCIONA (NO LO MODIFICAMOS)
from scrapers.carrefour_scraper_async import search as carrefour_search_by_name

BASE = "https://www.carrefour.com.ar"

async def search(ctx, barcode: str, limit: int = 3) -> List[Dict]:
    """
    1) Resuelve nombre desde EAN usando Carrefour.
    2) Delegamos al scraper por nombre (q) que ya está probado.
    """
    name: Optional[str] = await resolve_name_via_store(ctx, BASE, barcode)
    if not name:
        return []
    return await carrefour_search_by_name(ctx, q=name, barcode=None, limit=limit)
