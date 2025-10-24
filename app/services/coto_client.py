import os, time, threading, concurrent.futures
from typing import Optional, List, Dict
from collections import OrderedDict

# Solo la función pública del scraper
from scrapers.coto_scraper_async import search

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

    def search(self, q: str, limit: int = 10) -> List[Dict]:
        key = f"{q.lower().strip()}|{limit}"
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
def search_coto(q: str, limit: int = 10) -> List[Dict]:
    return coto_client.search(q, limit=limit)