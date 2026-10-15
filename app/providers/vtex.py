"""Tiendas VTEX (Jumbo, Carrefour, Día): API pública de catálogo, sin navegador."""
from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import quote

from app.providers.common import BARCODE_RE, fmt_price, only_digits, short_promo
from app.providers.http import get_json


class VtexStore:
    def __init__(self, name: str, base: str):
        self.name = name
        self.base = base.rstrip("/")

    def _offer(self, product: Dict) -> Optional[Dict]:
        """Primer sku/seller con stock y precio > 0."""
        for sku in product.get("items") or []:
            for seller in sku.get("sellers") or []:
                co = seller.get("commertialOffer") or {}
                if (co.get("Price") or 0) > 0 and (co.get("AvailableQuantity") or 0) > 0:
                    return {"sku": sku, "co": co}
        return None

    def _promo(self, co: Dict) -> Optional[str]:
        for key in ("Teasers", "DiscountHighLight"):
            for t in co.get(key) or []:
                name = (t.get("name") or t.get("Name") or "").strip()
                if name:
                    return short_promo(name) or name
        # ListPrice no es confiable (Jumbo devuelve valores absurdos); PriceWithoutDiscount sí.
        price, base = co.get("Price") or 0, co.get("PriceWithoutDiscount") or 0
        if price and base > price * 1.02:
            pct = round((base - price) / base * 100)
            if 1 <= pct <= 80:
                return f"{pct}% OFF"
        return None

    def _to_item(self, p: Dict) -> Optional[Dict]:
        found = self._offer(p)
        if not found:
            return None
        sku, co = found["sku"], found["co"]
        slug = p.get("linkText")
        url = f"{self.base}/{slug}/p" if slug else (p.get("link") or "")
        if url.startswith("/"):
            url = self.base + url
        images = sku.get("images") or []
        ean = only_digits(sku.get("ean"))
        return {
            "title": " ".join((p.get("productName") or "").split()) or None,
            "price": fmt_price(co.get("Price")),
            "url": url,
            "image_url": images[0].get("imageUrl") if images else None,
            "barcode": ean if ean and 8 <= len(ean) <= 14 else None,
            "promotion": self._promo(co),
        }

    async def search(self, q: Optional[str], barcode: Optional[str], limit: int) -> List[Dict]:
        query = (barcode or q or "").strip()
        if not query:
            return []
        # VTEX rechaza '+' como espacio (400): hay que mandar %20, por eso armamos la query a mano.
        field = "fq" if BARCODE_RE.fullmatch(query) else "ft"
        value = f"alternateIds_Ean:{query}" if field == "fq" else query
        # pedimos de más: algunos productos no tienen stock y se descartan
        url = (
            f"{self.base}/api/catalog_system/pub/products/search"
            f"?{field}={quote(value, safe='')}&_from=0&_to={max(limit * 2, 6) - 1}"
        )
        data = await get_json(url)
        if not isinstance(data, list):
            return []
        items, seen = [], set()
        for p in data:
            it = self._to_item(p)
            if it and it["title"] and it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
                if len(items) >= limit:
                    break
        return items
