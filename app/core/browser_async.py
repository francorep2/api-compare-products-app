from contextlib import asynccontextmanager
from playwright.async_api import async_playwright

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
        self._pw = None
        self._browser = None

    async def startup(self, headless: bool = True):
        if self._pw is None:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=headless)

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None

    @asynccontextmanager
    async def new_context(self, **ctx_overrides):
        ctx = DEFAULT_CTX | ctx_overrides
        context = await self._browser.new_context(
            user_agent=ctx["user_agent"],
            locale=ctx["locale"],
            viewport=ctx["viewport"],
        )
        try:
            yield context
        finally:
            await context.close()

browser_manager_async = AsyncBrowserManager()
