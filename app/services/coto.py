from typing import List, Dict, Optional
import asyncio
from urllib.parse import quote, urljoin
from app.core.browser import browser_manager

BASE = "https://www.cotodigital3.com.ar"

def _coto_worker(q: Optional[str], barcode: Optional[str], limit: int) -> List[Dict]:
    query = barcode or q
    if not query:
        return []
    with browser_manager.new_context(
        locale="es-AR",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        viewport={"width": 1366, "height": 800},
    ) as ctx:
        page = ctx.new_page()
        url = f"{BASE}/sitios/cdigi/browse?Ntt={quote(query)}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # TODO: Ajustar a los selectores reales de Coto
        card_sel = ".product-tile, .producto, .vtex-product-summary-2-x-container"  # placeholders
        cards = page.locator(card_sel)
        n = min(cards.count(), limit)
        results: List[Dict] = []
        for i in range(n):
            card = cards.nth(i)
            title = None
            href = None
            img = None
            price_text = None

            if card.locator("a").count():
                href = card.locator("a").first.get_attribute("href")
            if card.locator("img").count():
                img = card.locator("img").first.get_attribute("src")
            if card.locator("h3, h2, .product-title").count():
                title = card.locator("h3, h2, .product-title").first.inner_text()

            # si hay price visible
            if card.locator(".price, .product-price, .shelf__price").count():
                price_text = card.locator(".price, .product-price, .shelf__price").first.inner_text()

            results.append({
                "title": (title or "").strip() or None,
                "price": (price_text or "").strip() or None,
                "url": urljoin(BASE, href) if href else None,
                "image_url": img,
                "barcode": None,   # para EAN verdadero hay que abrir la PDP y leer ficha técnica
                "store": "coto",
            })
        return results

async def search_coto(q: Optional[str]=None, barcode: Optional[str]=None, limit: int=3) -> List[Dict]:
    return await asyncio.to_thread(_coto_worker, q, barcode, limit)
