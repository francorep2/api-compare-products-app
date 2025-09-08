# app/core/browser_async.py
import asyncio
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright

DEFAULT_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

class AsyncBrowserManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None

    async def startup(self, headless: bool = True):
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=headless, args=DEFAULT_ARGS
                )

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    @asynccontextmanager
    async def new_context(self, **kwargs):
        if self._browser is None:
            await self.startup()
        ctx = await self._browser.new_context(**kwargs)
        try:
            yield ctx
        finally:
            await ctx.close()

browser_manager_async = AsyncBrowserManager()
