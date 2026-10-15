"""Coto Digital: usa el buscador Constructor.io que consume su propio sitio web."""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Dict, List, Optional
from urllib.parse import quote

from app.providers.common import fmt_price, only_digits, short_promo
from app.providers.http import get_json

API = "https://ac.cnstrc.com/search"
API_KEY = os.getenv("COTO_CONSTRUCTOR_KEY", "key_r6xzz4IAoTWcipni")
CLIENT_ID = "cio-fe-web-coto"
SUCURSAL = os.getenv("COTO_SUCURSAL_ID", "200")
SITE = "https://www.coto.com.ar"


def _slug(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class CotoStore:
    name = "coto"

    def _price(self, data: Dict) -> Optional[float]:
        """Precio unitario de góndola. OJO: `formatPrice` es el precio por unidad de
        medida (kilo, m², etc.), no el del producto; el unitario es `listPrice`
        (de la sucursal) o el `discountPrice` de la promo vigente."""
        prices = data.get("price") or []
        mine = next((p for p in prices if p.get("store") == SUCURSAL), None) or (prices[0] if prices else None)
        regular = (mine or {}).get("listPrice") or data.get("product_list_price")
        if not regular:
            return None
        best = float(regular)
        for d in data.get("discounts") or []:
            m = re.search(r"[\d.]+", str(d.get("discountPrice") or "").replace(",", ""))
            if not m:
                continue
            try:
                promo = float(m.group(0))
            except ValueError:
                continue
            if 0.2 * float(regular) <= promo < best:  # descarta valores absurdos
                best = promo
        return best

    def _promo(self, data: Dict) -> Optional[str]:
        for d in data.get("discounts") or []:
            txt = d.get("discountText") or d.get("takingText")
            if txt:
                return short_promo(txt) or str(txt).strip()
        return None

    def _to_item(self, r: Dict) -> Optional[Dict]:
        data = r.get("data") or {}
        title = r.get("value") or data.get("sku_display_name")
        price = fmt_price(self._price(data))
        avail = data.get("store_availability")
        if not title or not price or (avail and SUCURSAL not in avail):
            return None
        pdp = (data.get("url") or "").replace("format=json", "")
        ean = only_digits(data.get("product_main_ean"))
        return {
            "title": " ".join(title.split()),
            "price": price,
            "url": f"{SITE}/productos/{_slug(title)}/{pdp}" if pdp else SITE,
            "image_url": data.get("product_large_image_url") or data.get("image_url"),
            "barcode": ean if ean and 8 <= len(ean) <= 14 else None,
            "promotion": self._promo(data),
        }

    async def search(self, q: Optional[str], barcode: Optional[str], limit: int) -> List[Dict]:
        query = (barcode or q or "").strip()
        if not query:
            return []
        data = await get_json(
            f"{API}/{quote(query, safe='')}",
            {
                "key": API_KEY,
                "c": CLIENT_ID,
                "section": "Products",
                "num_results_per_page": str(max(limit * 2, 6)),
                "filters[store_availability]": SUCURSAL,
            },
        )
        results = ((data or {}).get("response") or {}).get("results") or []
        items = [it for it in map(self._to_item, results) if it]
        return items[:limit]
