# app/core/browser.py
from contextlib import contextmanager
from playwright.sync_api import sync_playwright

class BrowserManager:
    def __init__(self):
        self._pw = None
        self._browser = None

    def startup(self, headless: bool = True):
        if self._pw is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=headless)

    def shutdown(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None

    @contextmanager
    def new_context(self, **kwargs):
        """
        Uso:
            with browser_manager.new_context(locale="es-AR", user_agent="...") as ctx:
                # usá ctx dentro del scraper (ctx.new_page(), etc.)
        """
        if not self._browser:
            raise RuntimeError("BrowserManager no inicializado. Llamá startup() primero.")
        ctx = self._browser.new_context(**kwargs)
        try:
            yield ctx
        finally:
            ctx.close()

browser_manager = BrowserManager()
