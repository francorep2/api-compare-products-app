# app/services/coto.py
from scrapers.coto_scraper_async import search_coto as coto_core_search

async def search_coto(q=None, barcode=None, limit=3):
    return await coto_core_search(q=q, barcode=barcode, limit=limit)
