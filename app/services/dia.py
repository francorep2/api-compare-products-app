# app/services/dia.py
from typing import List, Dict, Optional
from app.core.browser import browser_manager
from scrapers.dia_scraper import search as dia_search

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

def search_dia(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    with browser_manager.new_context(**DEFAULT_CTX) as ctx:
        return dia_search(ctx, q=q, barcode=barcode, limit=limit)
