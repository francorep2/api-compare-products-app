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
            # Si el browser no existe o está desconectado, relanzar
            if self._browser is None or getattr(self._browser, "is_connected", lambda: False)() is False:
                self._browser = await self._pw.chromium.launch(headless=True)

    async def startup(self):
        await self._ensure_started()

    async def shutdown(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._pw:
                await self._pw.stop()
                self._pw = None

    @asynccontextmanager
    async def new_context(self, **ctx_options):
        await self._ensure_started()
        await self._ctx_sem.acquire()
        context = None
        try:
            context = await self._browser.new_context(**ctx_options)
            yield context
        except Exception:
            # Si falla por browser cerrado, intentar una vez recrear y reintentar
            try:
                await self._ensure_started()
                context = await self._browser.new_context(**ctx_options)
                yield context
            finally:
                if context:
                    await context.close()
        else:
            if context:
                await context.close()
        finally:
            self._ctx_sem.release()

browser_manager_async = AsyncBrowserManager(max_contexts=2)
