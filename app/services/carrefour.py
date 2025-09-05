# app/services/carrefour.py
from typing import List, Dict, Optional
from app.core.browser import browser_manager

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120 Safari/537.36"),
    "viewport": {"width": 1366, "height": 800},
}

# Intento 1: importar 'search' (q o barcode)
# Intento 2: caer a 'search_top_n' (solo texto)
try:
    from scrapers.carrefour_scraper import search as _carrefour_search
    _HAS_BARCODE_SEARCH = True
except ImportError:
    from scrapers.carrefour_scraper import search_top_n as _carrefour_search
    _HAS_BARCODE_SEARCH = False


def search_carrefour(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    """
    - Si el scraper expone `search(ctx, q, barcode, limit)`, lo usa.
    - Si no, cae a `search_top_n(ctx, q, limit)` ignorando barcode.
    """
    with browser_manager.new_context(**DEFAULT_CTX) as ctx:
        if _HAS_BARCODE_SEARCH:
            return _carrefour_search(ctx, q=q, barcode=barcode, limit=limit)
        # fallback: solo búsqueda por texto
        return _carrefour_search(ctx, q or "", limit=limit)
