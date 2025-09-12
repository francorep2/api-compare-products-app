from app.services.vtex_ean import vtex_search_ean
from app.services.vtex_price_http import enrich_vtex_prices_http
import logging
log = logging.getLogger(__name__)

async def search_jumbo_barcode(barcode: str, limit: int = 3):
    base = "https://www.jumbo.com.ar"
    try:
        items = await vtex_search_ean(base, barcode, limit)
    except Exception as e:
        log.warning(f"[JUMBO] vtex_search_ean error ean={barcode}: {e}")
        return []
    if not items:
        return []
    try:
        items = await enrich_vtex_prices_http(items)
    except Exception as e:
        log.warning(f"[JUMBO] enrich_vtex_prices_http error: {e}")
    return items or []
