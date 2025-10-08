from typing import List, Optional
import requests

# Importa el modelo de datos y la función del scraper
from app.models.search_item import SearchItem
from scrapers.coto_scraper_async import search_coto as search_coto_scraper

async def search(q: Optional[str] = None, limit: Optional[int] = None) -> List[SearchItem]:
    if not q:
        return []

    try:
        products = search_coto_scraper(search_term=q)

        if limit and limit > 0:
            return products[:limit]

        return products

    except requests.exceptions.RequestException as e:
        print(f"Service Layer Error: No se pudo conectar con Coto. {e}")
        raise e  # Relanza la excepción
    except Exception as e:
        print(f"Service Layer Error: Ocurrió un error inesperado. {e}")
        raise e  # Relanza la excepción

