# app/core/browser_async.py
from contextlib import asynccontextmanager
from playwright.async_api import (
    async_playwright, Playwright, Browser, BrowserContext, Page
)

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

class AsyncBrowserManager:
    
    def __init__(self):
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        
    async def startup(self, headless: bool = True):
        print("[BrowserManager] Iniciando Playwright...")
        if self._pw is None:
            self._pw = await async_playwright().start()
            # ← flags clave en Koyeb
            self._browser = await self._pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        print("[BrowserManager] Browser iniciado.")

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None
        print("[BrowserManager] Browser detenido.")

    @asynccontextmanager
    async def context(self, context_options: dict | None = None) -> BrowserContext:
        """
        Proporciona un contexto de navegador seguro que se auto-inicia y se auto-cierra.
        """
        if self._browser is None:
            await self.startup(headless=True)
        
        options = context_options or DEFAULT_CTX
        ctx = await self._browser.new_context(**options)
        
        try:
            yield ctx
        finally:
            await ctx.close()

    @asynccontextmanager
    async def page(self) -> Page:
        """
        Proporciona una página de un contexto nuevo.
        """
        async with self.context() as ctx:
            page = await ctx.new_page()
            try:
                yield page
            finally:
                pass # El 'with self.context()' ya la cierra.

# Creamos una instancia global
browser_manager_async = AsyncBrowserManager()