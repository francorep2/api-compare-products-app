# app/services/coto_client.py
import os, time, threading
from typing import Optional, List
from collections import OrderedDict

# Importá SOLO lo que ya expone tu scraper
from scrapers.coto_scraper_async import (
    _build_session, _bootstrap_cookies, _build_search_urls,
    _extract_preloaded_schema, _extract_ldjson_schema, _extract_html_schema_fallback,
    DEFAULT_TIMEOUT,
)

class _TTLCache:
    def __init__(self, ttl: float = 90.0, maxsize: int = 256):
        self.ttl = ttl
        self.maxsize = maxsize
        self.data: OrderedDict[str, tuple[float, List[dict]]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, k: str) -> Optional[List[dict]]:
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

    def set(self, k: str, val: List[dict]):
        with self.lock:
            self.data[k] = (time.time(), val)
            self.data.move_to_end(k)
            while len(self.data) > self.maxsize:
                self.data.popitem(last=False)

class CotoClient:
    """
    Reusa UNA sesión HTTP por worker, hace bootstrap 1 vez,
    cachea resultados por TTL y aplica un presupuesto por request.
    """
    def __init__(self, ttl_seconds: float = 90.0, per_req_budget: float = 9.5):
        self.s = _build_session()
        _bootstrap_cookies(self.s)
        self.cache = _TTLCache(ttl_seconds, 256)
        self.lock = threading.Lock()
        self.budget = per_req_budget

    def _try_url(self, url: str, limit: int) -> Optional[List[dict]]:
        try:
            r = self.s.get(url, timeout=DEFAULT_TIMEOUT)
            status = getattr(r, "status_code", None) or getattr(r, "status", 0)
            body = r.text if hasattr(r, "text") else r.content.decode("utf-8", "ignore")
        except Exception:
            return None
        if status >= 400 or ("Access Denied" in body) or ("captcha" in body.lower()):
            return None

        # Los 3 parsers de tu scraper "schema-full"
        prods = (_extract_preloaded_schema(body)
                 or _extract_ldjson_schema(body)
                 or _extract_html_schema_fallback(body, limit=limit))
        return prods[:limit] if prods else []

    def search(self, q: str, limit: int = 10) -> List[dict]:
        key = f"{q.lower().strip()}|{limit}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # Evita N requests iguales simultáneas
        with self.lock:
            again = self.cache.get(key)
            if again is not None:
                return again

            t0 = time.monotonic()
            for url in _build_search_urls(q):
                if time.monotonic() - t0 > self.budget:
                    break
                res = self._try_url(url, limit)
                if res:
                    self.cache.set(key, res)
                    return res

            # Re-bootstrap rápido si aún queda tiempo del presupuesto
            if (self.budget - (time.monotonic() - t0)) > 2.0:
                _bootstrap_cookies(self.s)
                for url in _build_search_urls(q):
                    res = self._try_url(url, limit)
                    if res:
                        self.cache.set(key, res)
                        return res

            self.cache.set(key, [])
            return []

# Instancia global (1 por worker)
coto_client = CotoClient(
    ttl_seconds=float(os.getenv("COTO_SEARCH_TTL", "90")),
    per_req_budget=float(os.getenv("COTO_BUDGET_SECONDS", "9.5")),
)