from app.services.vtex_ean import vtex_search_ean
from app.services.vtex_price import enrich_vtex_prices

async def search_dia_barcode(barcode: str, limit: int = 3):
    base = "https://diaonline.supermercadosdia.com.ar"
    items = await vtex_search_ean(base, barcode, limit)
    items = await enrich_vtex_prices(items)
    return items
