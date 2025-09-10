# app/scrapers/vtex_helper.py
from typing import List, Dict
from urllib.parse import urljoin

def parse_vtex_hits(products: list, store_name: str, base_url: str, limit: int) -> List[Dict]:
    hits = []
    for p in products[:limit]:
        title = p.get("productName") or p.get("productTitle") or p.get("productNameWithVariations")
        url = p.get("link") or p.get("linkText") or p.get("linkUrl")
        if url and url.startswith("/"):
            url = urljoin(base_url, url)

        image_url = None
        items = p.get("items") or []
        if items:
            imgs = items[0].get("images") or []
            if imgs:
                image_url = imgs[0].get("imageUrl")

        # EAN (si aparece)
        barcode = None
        # Muchas tiendas no lo exponen en este JSON; lo dejamos opcional
        hits.append({
            "title": title,
            "price": None,
            "url": url,
            "image_url": image_url,
            "barcode": barcode,
            "store": store_name,
        })
    return hits
