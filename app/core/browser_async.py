from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

def should_block_request(route):
    """Función para decidir qué recursos bloquear."""
    return route.request.resource_type in {"image", "stylesheet", "font", "media"}

class AsyncBrowserManager:
    def __init__(self):
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

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
        if not self._browser:
            raise Exception("El navegador no ha sido iniciado.")
            
        ctx_options = {**DEFAULT_CTX, **ctx_overrides}
        context = await self._browser.new_context(**ctx_options)
        
        # --- MEJORA DE VELOCIDAD AÑADIDA AQUÍ ---
        # Bloqueamos los recursos innecesarios antes de usar el contexto.
        await context.route("**/*", lambda route: route.abort() if should_block_request(route) else route.continue_())
        
        try:
            yield context
        finally:
            await context.close()

browser_manager_async = AsyncBrowserManager()