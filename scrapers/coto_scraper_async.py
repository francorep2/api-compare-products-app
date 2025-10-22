from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import re, json, html, unicodedata, time, logging
import urllib.parse as _url
from curl_cffi import requests as crequests
from curl_cffi.requests import Session

log = logging.getLogger("scraper.coto")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

FAST_MODE_DEFAULT = True
TEXT_ONLY_DEFAULT = True
ENRICH_BARCODE_DEFAULT = False
_DEFAULT_TIMEOUT = 9

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)

def _build_session(impersonate="chrome") -> Session:
    s = crequests.Session(impersonate=impersonate, timeout=_DEFAULT_TIMEOUT)
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": f"{COTO_BASE}/sitios/cdigi/home?idSucursal={COTO_SUCURSAL_ID}&Dy=1",
        "sec-ch-ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    })
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
    return s

# sesión inicial
_SESSION: Session = _build_session("chrome")

def _get(url: str, timeout: Optional[float] = None) -> Tuple[str, int]:
    r = _SESSION.get(url, timeout=timeout or _DEFAULT_TIMEOUT)
    return r.text, r.status_code

def search_coto_requests(q: str, limit: int = TOP_N, *,
                         fast: bool = FAST_MODE_DEFAULT,
                         text_only: bool = TEXT_ONLY_DEFAULT,
                         enrich_barcode: bool = ENRICH_BARCODE_DEFAULT) -> List[Dict]:
    global _SESSION

    def _do_search() -> Tuple[str, int, str]:
        url = f"{COTO_BASE}/sitios/cdigi/busqueda?Ntt={_url.quote(q)}&idSucursal={COTO_SUCURSAL_ID}&Dy=1&Nrpp=12"
        html_text, status = _get(url)
        blocked = ("Access Denied" in html_text) or ("captcha" in html_text.lower())
        return html_text, status, "blocked" if blocked else "ok"

    html_text, status, flag = _do_search()

    if flag == "blocked":
        log.warning("[COTO] bloqueado con fingerprint 'chrome', reintentando con chrome124")
        _SESSION.close()
        _SESSION = _build_session("chrome124")
        html_text, status, flag = _do_search()

    if flag == "blocked" or status >= 400:
        log.warning(f"[COTO] status={status} bloqueado=True len={len(html_text)}")
        return []

    # === PARSER EXPRESS ===
    items = re.findall(r'title="([^"]+)".*?\$ ?([0-9.,]+).*?href="(/sitios/cdigi/productos/[^"]+)"', html_text)
    results = []
    for title, price, href in items[:limit]:
        results.append({
            "title": title.strip(),
            "price": f"ARS {price}",
            "url": f"{COTO_BASE}{href}",
            "image_url": "",
            "barcode": None,
            "promotion": None,
        })

    log.info(f"[COTO] {len(results)} resultados en {q!r} (status={status})")
    return results