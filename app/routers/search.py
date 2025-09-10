# app/routes/search.py
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Dict, List
import asyncio
from app.schemas.shopping_list import SearchHit
from app.services import carrefour, coto, dia, jumbo

router = APIRouter(prefix="/search", tags=["search"])

STORES = {
    "carrefour": carrefour.search_carrefour,
    "coto": coto.search_coto,
    "dia": dia.search_dia,
    "jumbo": jumbo.search_jumbo,
}

async def _run_store(fn, q, barcode, limit, timeout_s=30):
    try:
        return await asyncio.wait_for(fn(q=q, barcode=barcode, limit=limit), timeout=timeout_s)
    except Exception as e:
        return {"error": str(e)}

@router.get("/all-barcode")
async def search_all(q: Optional[str] = Query(None), barcode: Optional[str] = Query(None), limit: int = Query(3, ge=1, le=10)):
    if not q and not barcode:
        raise HTTPException(400, detail="Debes enviar `q` o `barcode`")
    tasks = [ _run_store(fn, q, barcode, limit) for fn in STORES.values() ]
    results = await asyncio.gather(*tasks)
    payload: Dict[str, List[SearchHit]] = { name: [] for name in STORES.keys() }
    debug = []

    for (name, res) in zip(STORES.keys(), results):
        if isinstance(res, dict) and "error" in res:
            debug.append({"store": name, "phase": "error", "error": res["error"]})
            continue
        # res debe ser list[dict] o list[SearchHit]
        hits = []
        for item in res or []:
            try:
                hits.append(SearchHit(store=name, **item))
            except Exception as e:
                debug.append({"store": name, "phase": "parse_error", "error": str(e), "raw": item})
        # Si llegó barcode, filtrar por coincidencia de EAN si está presente
        if barcode:
            filtered = [h for h in hits if (h.barcode == barcode) or (h.barcode is None)]
            hits = filtered or hits
        payload[name] = hits

        debug.append({"store": name, "phase": "ok" if hits else "no_hits", "count": len(hits)})

    count = sum(len(v) for v in payload.values())
    return {"count": count, "results": payload, "debug": debug}

@router.get("/{store}")
async def search_store(store: str, q: Optional[str] = Query(None), barcode: Optional[str] = Query(None), limit: int = Query(3, ge=1, le=10)):
    store = store.lower()
    if store not in STORES:
        raise HTTPException(400, detail="store no soportada")
    try:
        data = await STORES[store](q=q, barcode=barcode, limit=limit)
        return {"results": data}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
