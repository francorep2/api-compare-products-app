# app/core/browser_async.py
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Playwright, Browser
import asyncio

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

class AsyncBrowserManager:
    def __init__(self, max_contexts: int = 2):
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx_sem = asyncio.Semaphore(max_contexts)
        self._lock = asyncio.Lock()

    async def _ensure_started(self):
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                # Flags recomendados para Koyeb/containers
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )

    async def _restart_browser(self):
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            await self._ensure_started()

    async def startup(self):
        await self._ensure_started()

    async def shutdown(self):
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    @asynccontextmanager
    async def new_context(self, **ctx_options):
        """Crea un BrowserContext y lo cierra SIEMPRE, incluso si hay excepción."""
        await self._ensure_started()
        await self._ctx_sem.acquire()
        context = None
        try:
            # 1er intento
            try:
                context = await self._browser.new_context(**ctx_options)
            except Exception:
                # Recrea browser y reintenta ANTES del yield
                await self._restart_browser()
                context = await self._browser.new_context(**ctx_options)

            # ÚNICO yield del context manager
            try:
                yield context
            finally:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
        finally:
            self._ctx_sem.release()

browser_manager_async = AsyncBrowserManager(max_contexts=2)
