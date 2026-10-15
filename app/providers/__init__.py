"""Proveedores de precios (una API HTTP por supermercado, sin navegador)."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Protocol

from app.providers import http
from app.providers.coto import CotoStore
from app.providers.vtex import VtexStore

log = logging.getLogger("providers")

STORE_TTL_SEC = 600  # 10 min: los precios no cambian más rápido que eso
STORE_TIMEOUT_SEC = 6.0


class Store(Protocol):
    name: str

    async def search(self, q: Optional[str], barcode: Optional[str], limit: int) -> List[Dict]: ...


STORES: Dict[str, Store] = {
    "coto": CotoStore(),
    "carrefour": VtexStore("carrefour", "https://www.carrefour.com.ar"),
    "jumbo": VtexStore("jumbo", "https://www.jumbo.com.ar"),
    "dia": VtexStore("dia", "https://diaonline.supermercadosdia.com.ar"),
}


async def search_store(
    store: str, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3
) -> List[Dict]:
    """Busca en una tienda. Nunca lanza: ante error o timeout devuelve []."""
    provider = STORES[store]
    key = f"{store}|{(barcode or '').strip()}|{' '.join((q or '').lower().split())}|{limit}"

    async def _run() -> List[Dict]:
        return await asyncio.wait_for(provider.search(q, barcode, limit), STORE_TIMEOUT_SEC)

    try:
        result = await http.cached(key, STORE_TTL_SEC, _run)
        if not result:
            http.cache_drop(key)  # un vacío puede ser un fallo transitorio: no lo cacheamos
        return result
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] búsqueda falló (%s): %r", store, key, e)
        return []


async def warm_up() -> None:
    """Abre conexiones TLS/HTTP2 a cada host al arrancar para que la 1ª búsqueda sea rápida."""

    async def _ping(url: str) -> None:
        try:
            await http.get_client().head(url, timeout=4)
        except Exception:  # noqa: BLE001
            pass

    await asyncio.gather(*[
        _ping(u)
        for u in (
            "https://www.carrefour.com.ar",
            "https://www.jumbo.com.ar",
            "https://diaonline.supermercadosdia.com.ar",
            "https://ac.cnstrc.com",
        )
    ])


async def close_http() -> None:
    await http.close_client()
