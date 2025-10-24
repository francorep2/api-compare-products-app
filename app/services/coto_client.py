import os, time, threading, concurrent.futures
from typing import Optional, List, Dict
from collections import OrderedDict

# Solo la función pública del scraper
from scrapers.coto_scraper_async import search

# ---------- Helpers: normalizar y adjuntar barcode ----------
import re as _re

def _pick_barcode_from_item(item: Dict) -> Optional[str]:
    """
    Intenta obtener un código de barras (EAN/GTIN) desde el schema o el _raw.
    Devuelve un string con dígitos si encuentra alguno, sino None.
    """
    # Fuentes directas en schema
    for k in ("barcode", "gtin", "gtin13", "gtin14", "gtin12", "ean"):
        v = item.get(k)
        if isinstance(v, (str, int)):
            s = _re.sub(r"\D", "", str(v))
            if 8 <= len(s) <= 18:
                return s or None

    # Buscar dentro de offers (a veces viene ahí)
    offers = item.get("offers") or {}
    if isinstance(offers, dict):
        for k in ("barcode", "gtin", "ean"):
            v = offers.get(k)
            if isinstance(v, (str, int)):
                s = _re.sub(r"\D", "", str(v))
                if 8 <= len(s) <= 18:
                    return s or None

    # Revisar _raw con claves típicas de Coto
    raw = item.get("_raw") or {}
    if isinstance(raw, dict):
        for k in ("ean", "barcode", "codigoDeBarras", "codigoDeBarra", "codigo_barras"):
            v = raw.get(k)
            if isinstance(v, (str, int)):
                s = _re.sub(r"\D", "", str(v))
                if 8 <= len(s) <= 18:
                    return s or None

    # A veces viene en arrays o strings con guiones
    for k in ("identifiers",):
        v = item.get(k) or (raw.get(k) if isinstance(raw, dict) else None)
        if isinstance(v, list):
            for e in v:
                s = _re.sub(r"\D", "", str(e))
                if 8 <= len(s) <= 18:
                    return s or None
        elif isinstance(v, str):
            s = _re.sub(r"\D", "", v)
            if 8 <= len(s) <= 18:
                return s or None

    return None

def _attach_barcode(items: List[Dict]) -> List[Dict]:
    """
    Asegura que cada item tenga la clave 'barcode' si se puede inferir.
    No pisa valores existentes válidos.
    """
    out: List[Dict] = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        if "barcode" in it and isinstance(it["barcode"], (str, int)):
            # normalizar a dígitos si es posible
            s = _re.sub(r"\D", "", str(it["barcode"]))
            it["barcode"] = s or it["barcode"]
            out.append(it)
            continue
        bc = _pick_barcode_from_item(it)
        if bc:
            it = dict(it)
            it["barcode"] = bc
        out.append(it)
    return out

def _norm_digits(v: Optional[str]) -> str:
    if v is None:
        return ""
    return _re.sub(r"\D", "", str(v))

# ---------- Cache TTL sencilla en memoria ----------
class _TTLCache:
    def __init__(self, ttl: float = 90.0, maxsize: int = 256):
        self.ttl = ttl
        self.maxsize = maxsize
        self.data: OrderedDict[str, tuple[float, List[Dict]]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, k: str) -> Optional[List[Dict]]:
        now = time.time()
        with self.lock:
            v = self.data.get(k)
            if not v:
                return None
            ts, val = v
            if now - ts > self.ttl:
                self.data.pop(k, None)
                return None
            self.data.move_to_end(k)
            return val

    def set(self, k: str, val: List[Dict]):
        with self.lock:
            self.data[k] = (time.time(), val)
            self.data.move_to_end(k)
            while len(self.data) > self.maxsize:
                self.data.popitem(last=False)

# ---------- Cliente con presupuesto y cache ----------
class CotoClient:
    """
    Wrap sin tocar el scraper:
    - cache TTL por (query,limit)
    - presupuesto por request usando ThreadPoolExecutor con timeout
    """
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def __init__(self, ttl_seconds: float = 90.0, per_req_budget: float = 9.5):
        self.cache = _TTLCache(ttl_seconds, 256)
        self.lock = threading.Lock()
        self.budget = per_req_budget

    def search(self, q: str, limit: int = 10, barcode: Optional[str] = None) -> List[Dict]:
        key = f"{q.lower().strip()}|{limit}|{_norm_digits(barcode) if barcode else ''}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        with self.lock:
            again = self.cache.get(key)
            if again is not None:
                return again

            # Ejecutamos el scraper en un thread y cortamos si excede el presupuesto
            future = self._executor.submit(search, q, limit)
            try:
                result = future.result(timeout=self.budget)  # corta a los N segundos
                result = result or []
                result = _attach_barcode(result)
                if barcode:
                    nb = _norm_digits(barcode)
                    if nb:
                        filtered = [it for it in result if _norm_digits(it.get("barcode")) == nb]
                        if filtered:
                            result = filtered
                        else:
                            # Intento de refuerzo: buscar usando el propio barcode como query
                            fut2 = self._executor.submit(search, nb, limit)
                            try:
                                res2 = fut2.result(timeout=max(1.5, self.budget / 2))
                                res2 = _attach_barcode(res2 or [])
                                filtered2 = [it for it in res2 if _norm_digits(it.get("barcode")) == nb]
                                if filtered2:
                                    result = filtered2
                            except Exception:
                                pass
            except concurrent.futures.TimeoutError:
                # No bloqueamos el request del API: devolvemos vacío y que el thread termine aparte
                result = []
            except Exception:
                result = []

            self.cache.set(key, result)
            return result

# Instancia global (1 por worker)
coto_client = CotoClient(
    ttl_seconds=float(os.getenv("COTO_SEARCH_TTL", "90")),
    per_req_budget=float(os.getenv("COTO_BUDGET_SECONDS", "9.5")),
)

# Wrapper tipo función (por compatibilidad con imports existentes)
def search_coto(q: str, limit: int = 10, barcode: Optional[str] = None) -> List[Dict]:
    return coto_client.search(q, limit=limit, barcode=barcode)