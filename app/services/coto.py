# app/services/coto.py
from typing import List, Dict, Optional
from app.core.browser import browser_manager
from scrapers.coto_scraper import search as coto_search

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    with browser_manager.new_context(**DEFAULT_CTX) as ctx:
        return coto_search(ctx, q=q, barcode=barcode, limit=limit)
