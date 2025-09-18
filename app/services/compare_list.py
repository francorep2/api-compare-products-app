# app/services/compare_list.py
from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
import asyncio
import re
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor

# Importá los servicios existentes (ya usados en tu app)
from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.jumbo import search_jumbo
from app.services.dia import search_dia

# ---------- Utils ----------

_CURRENCY_RE = re.compile(r"(?i)\b(ars|$)\b")
_SPACE_NBSP = ("\xa0", "\u00a0")

def _to_float_ars(price_text: Optional[str]) -> Optional[float]:
    """
    Normaliza precios tipo:
      "ARS 1.234,50"  "$ 1.234,50"  "1.234,50"  "1234.50"  "1 234,50"
    Devuelve float o None.
    """
    if not price_text:
        return None
    t = str(price_text)
    # Limpiezas básicas
    for s in _SPACE_NBSP:
        t = t.replace(s, " ")
    t = t.strip()

    # Sacar prefijos de moneda y símbolos
    t = t.replace("ARS", "").replace("ars", "").replace("$", "").replace("USD", "").strip()

    # Unificar: si hay coma como decimal -> pasarlo a punto
    # Regla VTEQ Ar: "1.234,56" => 1234.56
    # Primero eliminamos separador de miles (., espacio)
    t = t.replace(" ", "")
    if "," in t and "." in t:
        # Formato latino "1.234,56": remover puntos de miles y cambiar coma a punto
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." not in t:
        # "1234,56" -> "1234.56"
        t = t.replace(",", ".")
    else:
        # Ya es "1234.56" o "1234"
        pass

    # Filtrar caracteres no numéricos (conservando . y -)
    t = re.sub(r"[^0-9.\-]", "", t)

    if t == "" or t == "." or t == "-":
        return None
    try:
        return float(Decimal(t))
    except (InvalidOperation, ValueError):
        return None

def _best_price_from_hit(hit: Dict[str, Any]) -> Optional[float]:
    """
    Usa 'price' si viene como texto, pero si tu scraper guarda promo (p.ej. 'promo_price'),
    elegimos el menor entre ambos.
    Campos esperados (opcionales):
      - price: str|float
      - promo_price: str|float|None
      - promotion.final_price: str|float (algunos scrapers lo guardan anidado)
    """
    candidates: List[Optional[float]] = []

    def as_float(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        return _to_float_ars(str(v))

    candidates.append(as_float(hit.get("price")))
    candidates.append(as_float(hit.get("promo_price")))

    promo = hit.get("promotion") or {}
    if isinstance(promo, dict):
        candidates.append(as_float(promo.get("final_price")))
        candidates.append(as_float(promo.get("price")))
        candidates.append(as_float(promo.get("discounted_price")))

    # Elegimos el mínimo válido
    candidates = [c for c in candidates if isinstance(c, (int, float))]
    return min(candidates) if candidates else None

@dataclass
class ListItem:
    name: str
    quantity: float = 1.0
    barcode: Optional[str] = None

@dataclass
class StoreQuote:
    store: str
    per_item: List[Dict[str, Any]]
    total: float

# ---------- Core comparison ----------

_STORES = {
    "coto": search_coto,
    "carrefour": search_carrefour,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

# Pesados (si alguno abre browser pesado en tu implementación async)
_HEAVY = {"carrefour", "dia"}

async def _query_store_for_item(executor: ThreadPoolExecutor, store: str, fn, item: ListItem, limit: int = 1) -> Dict[str, Any]:
    """
    Busca por barcode si está, si no por nombre. Retorna el mejor hit (o none).
    Corre en ThreadPool porque tus servicios son sync con Playwright context manager.
    """
    loop = asyncio.get_event_loop()

    def run_call(barcode_first: bool):
        # Priorizamos barcode si existe
        if barcode_first and item.barcode:
            res = fn(q=None, barcode=item.barcode, limit=limit)
            if res:
                return res
        # Fallback: por nombre
        return fn(q=item.name, barcode=None, limit=limit)

    # 1) barcode primero (si hay)
    hits = await loop.run_in_executor(executor, run_call, True)
    # Si al buscar por barcode no trajo nada y tenemos nombre, probá por nombre
    if (not hits) and item.name:
        hits = await loop.run_in_executor(executor, run_call, False)

    # Normalizamos a primer hit
    hit = hits[0] if (isinstance(hits, list) and hits) else None
    if not hit:
        return {"found": False, "store": store}

    price = _best_price_from_hit(hit)
    return {
        "found": price is not None,
        "store": store,
        "match": {
            "title": hit.get("title"),
            "url": hit.get("url"),
            "image_url": hit.get("image_url"),
            "barcode": hit.get("barcode"),
            "raw_price": hit.get("price"),
            "raw_promo": hit.get("promotion"),
            "unit_price": price,
        },
    }

async def _quote_store(executor: ThreadPoolExecutor, store: str, items: List[ListItem], heavy_sem: asyncio.Semaphore) -> StoreQuote:
    fn = _STORES[store]

    async def quote_one(it: ListItem):
        # Serializamos pesados para evitar abrir múltiples browsers/contexts a la vez
        if store in _HEAVY:
            async with heavy_sem:
                return await _query_store_for_item(executor, store, fn, it, limit=1)
        else:
            return await _query_store_for_item(executor, store, fn, it, limit=1)

    results = await asyncio.gather(*[quote_one(it) for it in items], return_exceptions=False)

    per_item: List[Dict[str, Any]] = []
    total = 0.0
    for it, r in zip(items, results):
        if r.get("found") and r.get("match", {}).get("unit_price") is not None:
            unit = float(r["match"]["unit_price"])
            line_total = unit * float(it.quantity or 1)
            total += line_total
            per_item.append({
                "name": it.name,
                "barcode": it.barcode,
                "qty": it.quantity,
                "unit_price": unit,
                "line_total": round(line_total, 2),
                "title": r["match"]["title"],
                "url": r["match"]["url"],
                "image_url": r["match"]["image_url"],
            })
        else:
            per_item.append({
                "name": it.name,
                "barcode": it.barcode,
                "qty": it.quantity,
                "unit_price": None,
                "line_total": None,
                "title": None,
                "url": None,
                "image_url": None,
                "missing": True,
            })

    return StoreQuote(store=store, per_item=per_item, total=round(total, 2))

async def compare_list_prices(items: List[ListItem]) -> Dict[str, Any]:
    if not items:
        return {"currency": "ARS", "best_store": None, "best_total": 0.0, "stores": {}}

    heavy_sem = asyncio.Semaphore(1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        quotes = await asyncio.gather(
            *[_quote_store(executor, s, items, heavy_sem) for s in _STORES.keys()],
            return_exceptions=False
        )

    stores: Dict[str, Any] = {q.store: {"total": q.total, "items": q.per_item} for q in quotes}

    # elegir el más barato
    valid_totals = [(k, v["total"]) for k, v in stores.items()]
    valid_totals.sort(key=lambda kv: kv[1])

    best_store = valid_totals[0][0] if valid_totals else None
    best_total = valid_totals[0][1] if valid_totals else 0.0

    # agregar save directamente en cada store
    for s, v in stores.items():
        v["save"] = round(best_total - v["total"], 2)

    return {
        "currency": "ARS",
        "best_store": best_store,
        "best_total": round(best_total, 2),
        "stores": stores,
    }

