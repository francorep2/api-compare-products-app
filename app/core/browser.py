# app/core/browser.py
from contextlib import contextmanager
from playwright.sync_api import sync_playwright
import threading

DEFAULT_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

class BrowserManager:
    def __init__(self):
        # Un TLS por hilo para p/pw y browser
        self._local = threading.local()

    def _ensure(self, headless: bool = True):
        """Asegura que en ESTE hilo haya un playwright y un browser vivos."""
        pw = getattr(self._local, "pw", None)
        br = getattr(self._local, "br", None)
        if pw is None or br is None:
            pw = sync_playwright().start()
            br = pw.chromium.launch(headless=headless, args=DEFAULT_ARGS)
            self._local.pw = pw
            self._local.br = br

    def startup(self, headless: bool = True):
        """Opcional: precalienta browser en el hilo actual."""
        self._ensure(headless=headless)

    def shutdown(self):
        """Cierra browser y playwright del hilo actual (si existen)."""
        br = getattr(self._local, "br", None)
        pw = getattr(self._local, "pw", None)
        try:
            if br:
                br.close()
        finally:
            self._local.br = None
        try:
            if pw:
                pw.stop()
        finally:
            self._local.pw = None

    @contextmanager
    def new_context(self, headless: bool = True, **kwargs):
        """
        Crea un browser context nuevo y lo cierra al salir.
        Usa el browser del hilo actual; si no existe, lo crea.
        """
        self._ensure(headless=headless)
        ctx = self._local.br.new_context(**kwargs)
        try:
            yield ctx
        finally:
            try:
                ctx.close()
            except Exception:
                pass

# Instancia global reusada por tus servicios
browser_manager = BrowserManager()
