from typing import List, Dict, Optional
from app.core.browser_async import browser_manager_async
from scrapers.coto_scraper_async import search as coto_search_top_n
import json, re

async def _json_parse_safe(txt: str):
    try:
        return json.loads(txt)
    except Exception:
        return None

async def _page_contains_ean(page, ean: str) -> bool:
    try:
        scripts = await page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "nodes => nodes.map(n => n.textContent)"
        )
    except Exception:
        scripts = []
    
    for s in scripts or []:
        data = await _json_parse_safe(s)
        if not data:
            continue

        def has_gtin(obj) -> bool:
            if not isinstance(obj, dict):
                return False
            g = obj.get("gtin13") or obj.get("gtin") or obj.get("gtin14") or obj.get("gtin12")
            return str(g) == str(ean)

        if isinstance(data, list):
            if any(has_gtin(x) for x in data):
                return True
        else:
            if has_gtin(data):
                return True
    
    try:
        html = await page.content()
    except Exception:
        html = ""
    
    if html and re.search(rf"\b{re.escape(str(ean))}\b", html):
        return True

    return False

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}


# --- FUNCIÓN CORREGIDA ---
async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
   async with browser_manager_async.context(DEFAULT_CTX) as ctx:
    return await coto_search_top_n(ctx, q,barcode, limit=limit)