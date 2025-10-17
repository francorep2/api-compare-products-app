# app/core/browser_async.py
from __future__ import annotations
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page
import asyncio

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    # Viewport moderado para consumir menos RAM
    "viewport": {"width": 1200, "height": 800},
}

BLOCKED_TYPES = {"image", "stylesheet", "font", "media"}

def should_block_request(route):
    return route.request.resource_type in BLOCKED_TYPES

class AsyncBrowserManager:
    """
    Manager único para todo el proceso:
      - Inicializa Playwright + Chromium con flags seguros para contenedor
      - Mantiene UN Browser y UN Context compartido (recomendado en 512MB)
      - Provee pages a demanda, con bloqueo de recursos pesados
    """
    def __init__(self):
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._startup_lock = asyncio.Lock()
        self._page_semaphore = asyncio.Semaphore(4)  # limita concurrencia de páginas

    async def startup(self, headless: bool = True):
        # Idempotente + a prueba de carreras
        if self._browser and self._context:
            return
        async with self._startup_lock:
            if self._browser and self._context:
                return
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-dev-tools",
                ],
            )
            self._context = await self._browser.new_context(**DEFAULT_CTX)
            # timeouts razonables (ajustá si Coto está muy denso)
            self._context.set_default_timeout(20_000)              # 20s para acciones
            self._context.set_default_navigation_timeout(30_000)   # 30s para navegaciones
            # Bloqueo de recursos pesados a nivel de contexto
            await self._context.route("**/*", lambda r: r.abort() if should_block_request(r) else r.continue_())

    async def shutdown(self):
        # Cierra en orden inverso; idempotente
        try:
            if self._context:
                await self._context.close()
        finally:
            self._context = None
        try:
            if self._browser:
                await self._browser.close()
        finally:
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def ensure_started(self):
        if self._context is None:
            await self.startup(headless=True)

    @asynccontextmanager
    async def page(self) -> Page:
        """
        Entrega una Page lista para usar, respetando el límite de concurrencia.
        Uso:
            async with browser_manager_async.page() as p:
                await p.goto(url, wait_until="domcontentloaded")
        """
        await self.ensure_started()
        assert self._context is not None
        await self._page_semaphore.acquire()
        page = await self._context.new_page()
        try:
            yield page
        finally:
            await page.close()
            self._page_semaphore.release()

    # Si alguna ruta requiere aislamiento total (cookies, storage), usá este context manager:
    @asynccontextmanager
    async def isolated_context(self, **ctx_overrides):
        """
        Crea un contexto efímero SOLO si lo pedís explícitamente.
        Útil para flujos que necesitan storage limpio o UA distinto.
        """
        await self.ensure_started()
        base = DEFAULT_CTX.copy()
        base.update(ctx_overrides or {})
        context = await self._browser.new_context(**base)  # type: ignore
        await context.route("**/*", lambda r: r.abort() if should_block_request(r) else r.continue_())
        context.set_default_timeout(20_000)
        context.set_default_navigation_timeout(30_000)
        try:
            yield context
        finally:
            await context.close()

browser_manager_async = AsyncBrowserManager()
