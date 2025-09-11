from typing import Dict, List, Optional
from app.core.browser_async import browser_manager_async
from app.scrapers import jumbo_scraper_async as jumbo_search

async def search_jumbo(
    q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3
) -> List[Dict]:
    """Busca en Jumbo usando el scraper asincrónico."""
    async with browser_manager_async.get_browser_context() as context:
        results = await jumbo_search.search(context, q=q, barcode=barcode, limit=limit)
    
    # FIX DEFINITIVO:
    # Se elimina la clave 'store' de cada resultado para prevenir
    # errores de instanciación en el código que lo consume.
    for item in results:
        if isinstance(item, dict):
            item.pop('store', None)
            
    return results