from typing import List, Dict, Any
import httpx

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}

def _first(x: list):
    return x[0] if isinstance(x, list) and x else None

def _safe_price(item: Dict[str, Any]) -> float | None:
    try:
        sellers = item.get("sellers") or []
        seller0 = _first(sellers) or {}
        offer = (seller0.get("commertialOffer") or {})
        price = offer.get("Price")
        return float(price) if price is not None else None
    except Exception:
        return None

def vtex_search_by_ean(base_url: str, ean: str, limit: int = 3, timeout: float = 12.0) -> List[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/catalog_system/pub/products/search/?fq=alternateIds_RefId:{ean}"
    with httpx.Client(timeout=timeout, headers=HEADERS) as client:
        r = client.get(url)
        if r.status_code != 200:
            return []
        data = r.json() or []
        out: List[Dict[str, Any]] = []
        for p in data[:limit]:
            name = p.get("productName") or p.get("productTitle") or p.get("productNameComplete")
            link_text = p.get("linkText")
            product_url = f"{base_url.rstrip('/')}/{link_text}/p" if link_text else None

            items = p.get("items") or []
            sku0 = _first(items) or {}
            images = sku0.get("images") or []
            img0 = _first(images) or {}
            img_url = img0.get("imageUrl")

            ean_value = sku0.get("ean")
            if not ean_value:
                ref_ids = sku0.get("referenceId") or []
                ref0 = _first(ref_ids) or {}
                ean_value = ref0.get("Value") or ref0.get("value")

            price = _safe_price(sku0)

            out.append({
                "title": name,
                "price": price,
                "url": product_url,
                "image_url": img_url,
                "barcode": ean_value or ean,
            })
        return out
