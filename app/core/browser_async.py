# app/core/browser_async.py
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any
import asyncio

from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext

DEFAULT_CTX: Dict[str, Any] = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
    # headers opcionales
    "extra_headers": {
        "Accept-Language": "es-AR,es-419;q=0.9,es;q=0.8,en-US;q=0.7,en;q=0.6",
        "Upgrade-Insecure-Requests": "1",
    },
}

def _ctx_kwargs_from_options(opts: Dict[str, Any]) -> Dict[str, Any]:
    # mapea tus opciones al API de Playwright
    kwargs: Dict[str, Any] = {}
    if ua := opts.get("user_agent"):
        kwargs["user_agent"] = ua
    if vp := opts.get("viewport"):
        # viewport se pasa como width/height sueltos
        if isinstance(vp, dict):
            kwargs["viewport"] = {"width": int(vp.get("width", 1366)), "height": int(vp.get("height", 800))}
    if locale := opts.get("locale"):
        kwargs["locale"] = locale
    if headers := opts.get("extra_headers"):
        kwargs["extra_http_headers"] = headers
    # útil para sitios react con WAFs
    kwargs.setdefault("bypass_csp", True)
    kwargs.setdefault("ignore_https_errors", True)
    return kwargs

class AsyncBrowserManager:
    """
    Maneja un único Browser y genera Contexts desechables por request.
    """
    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._contexts: set[BrowserContext] = set()

    async def startup(self, headless: bool = True, **launch_kwargs):
        """
        Inicializa Playwright y el browser si aún no está listo.
        """
        if self._closed:
            raise RuntimeError("AsyncBrowserManager ya fue cerrado. Crea una nueva instancia.")
        async with self._lock:
            if self._browser is not None:
                return
            self._pw = await async_playwright().start()
            # flags útiles en contenedores headless
            default_args = {
                "headless": headless,
                "args": [
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            }
            default_args.update(launch_kwargs or {})
            self._browser = await self._pw.chromium.launch(**default_args)

    async def get_browser_context(self, options: Optional[Dict[str, Any]] = None) -> BrowserContext:
        """
        Crea y devuelve un BrowserContext nuevo con las opciones provistas.
        Es lo que tu servicio está esperando.
        """
        if self._browser is None:
            # intenta levantar con valores por defecto
            await self.startup(headless=True)
        opts = {**DEFAULT_CTX, **(options or {})}
        ctx_kwargs = _ctx_kwargs_from_options(opts)
        ctx = await self._browser.new_context(**ctx_kwargs)
        self._contexts.add(ctx)
        return ctx

    async def close_context(self, ctx: Optional[BrowserContext]):
        if not ctx:
            return
        try:
            await ctx.close()
        finally:
            self._contexts.discard(ctx)

    async def shutdown(self):
        """
        Cierra todos los contexts y el browser.
        """
        if self._closed:
            return
        self._closed = True
        # cerrar contexts abiertos
        for ctx in list(self._contexts):
            with contextlib.suppress(Exception):
                await ctx.close()
        self._contexts.clear()
        # cerrar browser y playwright
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._browser = None
        self._pw = None

    @asynccontextmanager
    async def context(self, options: Optional[Dict[str, Any]] = None):
        """
        Uso alternativo:
            async with browser_manager_async.context(DEFAULT_CTX) as ctx:
                ... usar ctx ...
        """
        ctx = await self.get_browser_context(options or {})
        try:
            yield ctx
        finally:
            await self.close_context(ctx)