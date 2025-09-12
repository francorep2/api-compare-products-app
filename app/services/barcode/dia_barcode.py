from typing import List, Dict
from app.services.vtex_ean import vtex_search_ean
BASE = "https://diaonline.supermercadosdia.com.ar"
async def search_dia_barcode(barcode: str, limit: int = 3) -> List[Dict]:
    return await vtex_search_ean(BASE, barcode, limit)