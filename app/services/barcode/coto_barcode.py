from typing import List, Dict
from app.services.vtex_ean import vtex_search_ean
BASE = "https://www.cotodigital3.com.ar"
async def search_coto_barcode(barcode: str, limit: int = 3) -> List[Dict]:
    return await vtex_search_ean(BASE, barcode, limit)