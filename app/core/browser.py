# app/core/browser.py
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Any, Dict
from playwright.async_api import async_playwright, Browser, Playwright

class AsyncBrowserManager:
    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._started = False

    async def ensure_started(self, **launch_kwargs) -> None:
        if self._browser is not None:
            return
        async with self._lock:
            if self._browser is not None:
                return
            if self._pw is None:
                self._pw = await async_playwright().start()
            # Lanzamos Chromium una sola vez y lo reusamos
            defaults = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            defaults.update(launch_kwargs or {})
            self._browser = await self._pw.chromium.launch(**defaults)
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                self._browser = None
                if self._pw is not None:
                    await self._pw.stop()
                self._pw = None
                self._started = False

    @asynccontextmanager
    async def new_context(self, **ctx_kwargs):
        await self.ensure_started()
        assert self._browser is not None
        context = await self._browser.new_context(**(ctx_kwargs or {}))
        try:
            yield context
        finally:
            try:
                await context.close()
            except Exception:
                pass

    # Compat: algunos códigos esperaban "new_async_context"
    def new_async_context(self, **ctx_kwargs):
        return self.new_context(**ctx_kwargs)

    # Compat opcional para ejecutar corrutinas desde código sync
    def run_async(self, coro):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # si ya hay loop (FastAPI uvicorn), devolver la corrutina para await externo
            return coro
        return loop.run_until_complete(coro)

browser_manager = AsyncBrowserManager()
