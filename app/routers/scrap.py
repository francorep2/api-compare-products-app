from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies.auth import get_current_user
from app.providers import search_store
from app.services.search import search_all as search_all_stores

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(get_current_user)])


def _single_store(store: str):
    async def endpoint(
        q: Optional[str] = Query(None),
        barcode: Optional[str] = Query(None),
        limit: int = Query(3, ge=1, le=10),
    ):
        return {"results": await search_store(store, q=q, barcode=barcode, limit=limit)}

    return endpoint


for _store in ("coto", "carrefour", "dia", "jumbo"):
    router.add_api_route(f"/{_store}", _single_store(_store), methods=["GET"], name=f"search_{_store}")


@router.get("/all")
async def search_all(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = Query(3, ge=1, le=10),
    enabled: List[str] = Query(default=["carrefour", "coto", "dia", "jumbo"]),
):
    if not (q and q.strip()) and not (barcode and barcode.strip()):
        raise HTTPException(status_code=400, detail="Debes enviar 'q' o 'barcode'.")
    results = await search_all_stores(q=q, barcode=barcode, limit=limit, stores=enabled)
    # Mantiene el contrato anterior: siempre las 4 claves.
    return {"results": {s: results.get(s, []) for s in ("coto", "carrefour", "dia", "jumbo")}}
