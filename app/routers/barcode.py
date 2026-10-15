from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import get_current_user
from app.services.search import normalize_stores, search_by_barcode

router = APIRouter(prefix="/search/barcode", tags=["barcode"], dependencies=[Depends(get_current_user)])


@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="repetir clave o CSV"),
):
    names = normalize_stores(enabled)
    results = await search_by_barcode(barcode, limit=limit, stores=names)
    total = sum(len(v) for v in results.values())
    return {"results": results, "count": total, "enabled": names, "barcode": barcode, "limit": limit}
