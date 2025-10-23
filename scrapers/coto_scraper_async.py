# scrapers/coto_scraper_async.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os, re, json, html
import urllib.parse as _url

# --- HTTP backend: curl_cffi si está, sino requests ---
try:
    from curl_cffi import requests as http
    HAVE_CURL = True
except Exception:
    import requests as http
    HAVE_CURL = False

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = int(os.getenv("COTO_SUCURSAL_ID", "200"))
DEFAULT_TIMEOUT = float(os.getenv("COTO_TIMEOUT", "8.0"))  # rápido y bajo 12s
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

COTO_DEBUG = os.getenv("COTO_DEBUG", "0") in {"1", "true", "TRUE", "yes", "on"}
def _dbg(*args):
    if COTO_DEBUG:
        try:
            print("[COTO]", *args, flush=True)
        except Exception:
            pass

def _build_session():
    s = http.Session(impersonate="chrome", timeout=DEFAULT_TIMEOUT) if HAVE_CURL else http.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es-419;q=0.9,es;q=0.8,en-US;q=0.7,en;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        # Hints de navegación reales
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Referer": f"{COTO_BASE}/sitios/cdigi/nuevositio",
        "sec-ch-ua": '"Google Chrome";v="120", "Chromium";v="120", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    })
    s.headers.update({
        "Priority": "u=0, i",
        "Accept-Charset": "utf-8",
        "Connection": "keep-alive",
    })
    # Cookie de sucursal desde el arranque
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
    return s

def _bootstrap_cookies(s) -> bool:
    """
    GET a /nuevositio para que setee cookies de sesión.
    """
    try:
        r = s.get(f"{COTO_BASE}/sitios/cdigi/nuevositio", timeout=DEFAULT_TIMEOUT)
        ok = (getattr(r, "status_code", None) or getattr(r, "status", 0)) < 400
        _dbg("bootstrap", "status=", getattr(r, "status_code", None) or getattr(r, "status", 0), "ok=", ok)
        return ok
    except Exception as e:
        _dbg("bootstrap error:", repr(e))
        return False

# ------------------- URLs de búsqueda -------------------

def _build_search_urls(q: str) -> list[str]:
    """
    Prioriza /categoria con Ntt (como me pasaste) y deja /busqueda como fallback.
    """
    urls = []

    # 1) Tu endpoint real
    p1 = {
        "_dyncharset": "utf-8",
        "Dy": "1",
        "Ntt": q,
        "idSucursal": str(COTO_SUCURSAL_ID),
    }
    urls.append(f"{COTO_BASE}/sitios/cdigi/categoria?{_url.urlencode(p1)}")

    # 2) Fallback histórico (a veces lo siguen sirviendo)
    p2 = {
        "Ntt": q,
        "idSucursal": str(COTO_SUCURSAL_ID),
        "Dy": "1",
        "Nrpp": "12",  # page pequeña
    }
    urls.append(f"{COTO_BASE}/sitios/cdigi/busqueda?{_url.urlencode(p2)}")
    return urls

# ------------------- Parsers: SOLO títulos -------------------

_PRELOADED_RE = re.compile(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*(?:&lt;/script&gt;|&lt;)", re.S)
_PRELOADED_JSONPARSE_RE = re.compile(r"__PRELOADED_STATE__\s*=\s*JSON\.parse\('(.*?)'\)\s*;?", re.S)
_PRELOADED_FLEX_RE = re.compile(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})", re.S)
_LDJSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S|re.I)

# Fallback HTMLs
_CARD_TITLE_RE = re.compile(r'title="([^"]+)"')
_H_TAG_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.S)
_GENERIC_NAME_RE = re.compile(r'class="[^"]*(?:product-name|product__name|item-name|product__title)[^"]*"[^>]*>(.*?)<', re.S|re.I)
_DATA_NAME_ATTR_RE = re.compile(r'data-name="([^"]+)"', re.I)
_DISPLAYNAME_RE = re.compile(r'"displayName"\s*:\s*"([^"]+)"')
_DATA_ANY_NAME_ATTR_RE = re.compile(r'data-[a-zA-Z_-]*name\s*=\s*"([^"]+)"', re.I)

def _extract_titles_from_state_obj(data: dict) -> list[str]:
    paths = [
        ["search", "results", "products"],
        ["results", "products"],
        ["search", "products"],
        ["products"],
    ]
    out: list[str] = []
    for path in paths:
        cur = data; ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False; break
        if ok and isinstance(cur, list):
            for p in cur:
                t = (p or {}).get("displayName") or (p or {}).get("name") or (p or {}).get("title")
                if t: out.append(str(t).strip())
            if out: break
    return out

def _extract_preloaded_titles(html_text: str) -> list[str]:
    # window.__PRELOADED_STATE__ = {...};
    m = _PRELOADED_RE.search(html_text)
    if m:
        try:
            raw = html.unescape(m.group(1))
            data = json.loads(raw)
            tit = _extract_titles_from_state_obj(data)
            if tit: return tit
        except Exception:
            pass
    # window.__PRELOADED_STATE__ = {...}  (sin &lt;/script&gt; explícito)
    mFlex = _PRELOADED_FLEX_RE.search(html_text)
    if mFlex and not (m and m.group(1)):
        try:
            raw = html.unescape(mFlex.group(1))
            data = json.loads(raw)
            tit = _extract_titles_from_state_obj(data)
            if tit: return tit
        except Exception:
            pass
    # window.__PRELOADED_STATE__ = JSON.parse('...');
    m2 = _PRELOADED_JSONPARSE_RE.search(html_text)
    if m2:
        try:
            raw = m2.group(1)
            raw = raw.encode("utf-8").decode("unicode_escape")
            data = json.loads(raw)
            tit = _extract_titles_from_state_obj(data)
            if tit: return tit
        except Exception:
            pass
    # fallback: rastrillar displayName plano en HTML
    brute = _DISPLAYNAME_RE.findall(html_text)[:50]
    return [t.strip() for t in brute if t and isinstance(t, str)]

def _extract_ldjson_titles(html_text: str) -> list[str]:
    out: list[str] = []
    for m in _LDJSON_RE.finditer(html_text):
        try:
            data = json.loads(html.unescape(m.group(1)))
        except Exception:
            continue
        if isinstance(data, dict):
            _collect_ldjson_titles_from_obj(data, out)
        elif isinstance(data, list):
            for obj in data:
                if isinstance(obj, dict):
                    _collect_ldjson_titles_from_obj(obj, out)
    return [t.strip() for t in out if t and isinstance(t, str)]

def _collect_ldjson_titles_from_obj(obj: dict, out: list[str]):
    t = obj.get("@type")
    if t == "ItemList" and isinstance(obj.get("itemListElement"), list):
        for el in obj["itemListElement"]:
            if isinstance(el, dict):
                item = el.get("item")
                if isinstance(item, dict):
                    name = item.get("name") or item.get("title")
                    if name: out.append(str(name))
    elif t in ("Product", "Offer"):
        name = obj.get("name") or obj.get("title")
        if name: out.append(str(name))

def _clean_text(x: str) -> str:
    x = re.sub(r"<[^>]+>", " ", x or "")
    x = re.sub(r"\s+", " ", x).strip()
    return x

def _extract_html_titles_fallback(html_text: str, limit: int) -> list[str]:
    titles: list[str] = []
    # 1) atributos title=""
    for m in _CARD_TITLE_RE.finditer(html_text):
        t = m.group(1)
        if t:
            titles.append(t.strip())
            if len(titles) >= limit: return titles
    # 2) <hN>
    for m in _H_TAG_RE.finditer(html_text):
        t = _clean_text(m.group(1) or "")
        if t:
            titles.append(t)
            if len(titles) >= limit: return titles
    # 3) clases comunes
    for m in _GENERIC_NAME_RE.finditer(html_text):
        t = _clean_text(m.group(1) or "")
        if t:
            titles.append(t)
            if len(titles) >= limit: return titles
    # 4) data-name=""
    for m in _DATA_NAME_ATTR_RE.finditer(html_text):
        t = m.group(1)
        if t:
            titles.append(t.strip())
            if len(titles) >= limit: return titles
    # 4b) data-*name variants
    for m in _DATA_ANY_NAME_ATTR_RE.finditer(html_text):
        t = m.group(1)
        if t:
            titles.append(t.strip())
            if len(titles) >= limit: return titles
    # 5) naive "name":""
    naive = re.findall(r'"name"\s*:\s*"([^"]+)"', html_text)[:limit*2]
    for t in naive:
        t = t.strip()
        if t and t not in titles:
            titles.append(t)
            if len(titles) >= limit: break
    return titles[:limit]

# ------------------- API pública -------------------

def search_coto_requests(query: str, limit: int = 10) -> list[str]:
    """
    Devuelve SOLO los títulos de productos para la búsqueda dada (list[str]).
    """
    s = _build_session()
    _bootstrap_cookies(s)

    def _try_url(session, url: str) -> list[str] | None:
        try:
            r = session.get(url, timeout=DEFAULT_TIMEOUT)
            status = getattr(r, "status_code", None) or getattr(r, "status", 0)
            text = r.text if hasattr(r, "text") else (r.content.decode("utf-8", "ignore"))
        except Exception:
            return None
        body = text or ""
        waf_markers = ("Access Denied", "Akamai", "Request unsuccessful", "Incapsula", "bot detection", "Verifying you are human", "rate limited")
        if status >= 400:
            _dbg("blocked status", status, "for", url)
            return None
        if any(m.lower() in body.lower() for m in waf_markers):
            _dbg("possible WAF interstitial for", url)
            # Return empty list so caller can try next URL instead of aborting flow
            return []
        # parsers
        titles = _extract_preloaded_titles(body)
        if titles:
            _dbg("preloaded hit", url, "n=", len(titles))
        if not titles:
            titles = _extract_ldjson_titles(body)
            if titles:
                _dbg("ldjson hit", url, "n=", len(titles))
        if not titles:
            titles = _extract_html_titles_fallback(body, limit=limit)
            if titles:
                _dbg("html fallback hit", url, "n=", len(titles))
        return titles[:limit] if titles else []

    # 1) categoria?...Ntt=
    for url in _build_search_urls(query):
        res = _try_url(s, url)
        if res is None:
            continue  # bloqueado o error, probar siguiente
        if res:
            return res

    # 2) Fallback a requests “plain” (sin curl_cffi) + bootstrap
    if HAVE_CURL:
        try:
            import requests as _plain
            s2 = _plain.Session()
            s2.headers.update(s.headers)
            s2.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
            s2.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
            _bootstrap_cookies(s2)
            for url in _build_search_urls(query):
                res = _try_url(s2, url)
                if res is None:
                    continue
                if res:
                    return res
        except Exception:
            pass

    # sin resultados (captcha/WAF o layout distinto)
    _dbg("no results for", query)
    return []

__all__ = ["search_coto_requests"]
