# app/services/peer_name_resolver.py
from typing import Optional, List, Dict

# Importá tus servicios async por EAN ya existentes
from app.services.barcode.jumbo_barcode import search_jumbo_barcode
from app.services.barcode.carrefour_barcode import search_carrefour_barcode
from app.services.barcode.dia_barcode import search_dia_barcode

CANDIDATE_MIN_LEN = 12  # evita títulos muy cortos tipo "Actimel 100g"

def _pick_best_title(items: List[Dict]) -> Optional[str]:
    # Se queda con el título más largo (suele ser el más descriptivo)
    titles = [x.get("title") for x in items if x.get("title")]
    titles = [t.strip() for t in titles if isinstance(t, str)]
    titles = [t for t in titles if len(t) >= CANDIDATE_MIN_LEN]
    if not titles:
        return None
    # Devolver el más largo
    return sorted(titles, key=len, reverse=True)[0]

async def resolve_product_name_from_peers(barcode: str) -> Optional[str]:
    """
    Intenta obtener un 'q' representativo consultando primero Jumbo/Carrefour/DIA por EAN.
    Devuelve un título para usar como query en COTO, o None si no puede inferirlo.
    """
    peers = [
        ("jumbo", search_jumbo_barcode),
        ("carrefour", search_carrefour_barcode),
        ("dia", search_dia_barcode),
    ]

    candidates: List[str] = []

    for _, func in peers:
        try:
            hits = await func(barcode=barcode, limit=3)
        except Exception:
            hits = []
        title = _pick_best_title(hits or [])
        if title:
            candidates.append(title)

    # Si nadie devolvió título decente, salimos
    if not candidates:
        return None

    # Heurística: elegir el más largo; si querés, podrías agregar más reglas acá
    return sorted(candidates, key=len, reverse=True)[0]
