"""Cliente HTTP compartido (keep-alive + HTTP/2) y utilidades de cache."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

try:  # HTTP/2 si el paquete h2 está instalado (httpx[http2]); si no, HTTP/1.1
    import h2  # noqa: F401
    _HTTP2 = True
except ImportError:  # pragma: no cover
    _HTTP2 = False

_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


def get_client() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    # las conexiones viven en un event loop: si cambió (tests, reload) se recrea el cliente
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client_loop = loop
        _client = httpx.AsyncClient(
            http2=_HTTP2,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(7.0, connect=3.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# Máximo de requests simultáneos por host (evita que una lista grande nos bloquee).
_HOST_LIMIT = 12
_host_sems: Dict[str, asyncio.Semaphore] = {}


def _sem_for(host: str) -> asyncio.Semaphore:
    sem = _host_sems.get(host)
    if sem is None:
        sem = _host_sems[host] = asyncio.Semaphore(_HOST_LIMIT)
    return sem


async def get_json(url: str, params: Optional[dict] = None, retries: int = 1) -> Any:
    """GET -> JSON. Acepta 200/206 (VTEX responde 206 en resultados parciales).
    Reintenta una vez ante timeouts / errores de red / 5xx."""
    host = httpx.URL(url).host
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            async with _sem_for(host):
                resp = await get_client().get(url, params=params)
            if resp.status_code in (200, 206):
                return resp.json()
            if resp.status_code < 500:
                return None
            last = RuntimeError(f"HTTP {resp.status_code}")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        if attempt < retries:
            await asyncio.sleep(0.15)
    raise last or RuntimeError("request failed")


# ---------------- cache TTL + coalescing de requests idénticas ----------------

_MAX_ENTRIES = 2000
_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, "asyncio.Future[Any]"] = {}


def _prune() -> None:
    now = time.monotonic()
    for k in [k for k, (exp, _) in _cache.items() if exp <= now]:
        _cache.pop(k, None)
    if len(_cache) > _MAX_ENTRIES:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: len(_cache) - _MAX_ENTRIES]:
            _cache.pop(k, None)


def cache_drop(key: str) -> None:
    _cache.pop(key, None)


async def cached(key: str, ttl: float, factory: Callable[[], Awaitable[Any]]) -> Any:
    """Devuelve el valor cacheado; si no existe lo calcula una sola vez aunque
    haya muchas llamadas concurrentes con la misma clave."""
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    fut = _inflight.get(key)
    if fut is not None:
        return await asyncio.shield(fut)
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        value = await factory()
        _cache[key] = (time.monotonic() + ttl, value)
        if len(_cache) > _MAX_ENTRIES:
            _prune()
        fut.set_result(value)
        return value
    except BaseException as e:
        if not fut.done():
            fut.set_exception(e)
            fut.exception()  # marca la excepción como consumida
        raise
    finally:
        _inflight.pop(key, None)
