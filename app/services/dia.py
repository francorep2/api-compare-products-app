from typing import List, Dict, Optional
import asyncio
from urllib.parse import quote
from app.core.browser import browser_manager
from app.scrapers.vtex_helper import parse_vtex_hits

BASE = "https://diaonline.supermercadosdia.com.ar"

def _dia_worker(q: Optional[str], barcode: Optional[str], limit: int) -> List[Dict]:
    query = barcode or q
    if not query:
        return []
    with browser_manager.new_context(
        locale="es-AR",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        viewport={"width": 1366, "height": 800},
    ) as ctx:
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass

        url = f"{BASE}/api/catalog_system/pub/products/search/?ft={quote(query)}&_from=0&_to={max(0,limit-1)}"
        resp = page.request.get(url, timeout=20000)
        if not resp.ok:
            return []
        data = resp.json()
        return parse_vtex_hits(data, "dia", BASE, limit)

async def search_dia(q: Optional[str]=None, barcode: Optional[str]=None, limit: int=3) -> List[Dict]:
    return await asyncio.to_thread(_dia_worker, q, barcode, limit)
