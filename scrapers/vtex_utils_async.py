# scrapers/vtex_utils_async.py
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

def _only_digits(s: str | None) -> str | None:
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    return d or None

def _normalize_url(u: str | None) -> str | None:
    if not u:
        return None
    try:
        p = urlparse(u)
        # forzamos https y /p canónico si existe
        path = p.path or ""
        if path.endswith("/p/"):
            path = path[:-1]
        # limpiamos query de tracking
        q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","_ga","sc"}]
        return urlunparse(("https", p.netloc.lower(), path, "", urlencode(q), ""))
    except:
        return u

def _normalize_title(t: str | None) -> str | None:
    if not t:
        return None
    t = re.sub(r"\s+", " ", t).strip()
    return t or None

def dedupe_and_trim(items: list[dict], limit: int) -> list[dict]:
    """
    Items: [{title, url, price, image_url, barcode, promotion}]
    - Filtra sin title/url
    - Normaliza title/url
    - Dedup por (url) o (title+barcode)
    - Aplica limit *después* de filtrar/normalizar
    """
    seen = set()
    out: list[dict] = []
    for it in items:
        title = _normalize_title(it.get("title"))
        url = _normalize_url(it.get("url"))
        if not title or not url:
            # descartamos tarjetas sin link o sin título
            continue

        barcode = _only_digits(it.get("barcode"))
        key = (url, title.lower(), barcode or "")
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "title": title,
            "url": url,
            "price": it.get("price"),
            "image_url": it.get("image_url"),
            "barcode": barcode,
            "promotion": it.get("promotion") or None,
        })
        if len(out) >= limit:
            break
    return out
