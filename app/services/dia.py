# app/service/dia.py
from typing import List, Dict
from urllib.parse import urlparse
from app.core.browser import browser_manager
from scrapers.dia_scraper import search_top_n as dia_search

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120 Safari/537.36"),
    "viewport": {"width": 1366, "height": 800},
}

# Bloquear trackers; permitir assets VTEX
def _should_abort(url: str) -> bool:
    u = url.lower()
    host = (urlparse(url).hostname or "").lower()
    allow_hosts = ("diaonline.supermercadosdia.com.ar", "vtexassets.com", "cdn.vtex.com", "akamaihd.net")
    if any(host.endswith(h) for h in allow_hosts):
        return False
    if any(s in u for s in [
        "analytics","gtm","google-analytics","facebook","doubleclick","hotjar",
        "segment","optimizely","fullstory","braze","pixel","adservice"
    ]):
        return True
    if u.endswith((".mp4",".webm",".mov")): return True
    return False

def search_dia(q: str, limit: int = 3) -> List[Dict]:
    with browser_manager.new_context(**DEFAULT_CTX) as ctx:
        ctx.route("**/*", lambda route: route.abort() if _should_abort(route.request.url) else route.continue_())
        return dia_search(ctx, q, limit=limit)
