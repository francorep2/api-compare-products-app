# app/services/coto.py
from typing import List, Dict, Optional

# 1. Importa la función 'search' del scraper.
# Asumo que tu scraper está en 'scrapers_async.coto_scraper_async'
# Si no, ajusta la ruta.
from scrapers.coto_scraper_async import search

async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    """
    Este es el servicio que llama tu router.
    Simplemente pasa la llamada a la función 'search' del scraper.
    """
    
    # 2. Llama a 'search' SIN pasarle 'ctx'.
    # La función 'search' del scraper maneja el browser y el timeout por sí misma.
    items = await search(q=q, barcode=barcode, limit=limit)
    return items