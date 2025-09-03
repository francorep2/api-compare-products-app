# app/services/carrefour.py
from typing import List, Dict
from app.core.browser import browser_manager

# Importá tu scraper:
from scrapers.carrefour_scraper import search_top_n as carrefour_search

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120 Safari/537.36"),
    "viewport": {"width": 1366, "height": 800},
}

def search_carrefour(q: str, limit: int = 3) -> List[Dict]:
    with browser_manager.new_context(**DEFAULT_CTX) as ctx:
        return carrefour_search(ctx, q, limit=limit)
