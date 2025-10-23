# coto_titles.py
# -*- coding: utf-8 -*-
"""
Scraper simple de Coto Digital: devuelve SOLO los títulos de producto para una búsqueda.
- Text-only, sin imágenes ni PDP.
- Usa cookie de sucursal para evitar bloqueos.
- Intenta extraer primero del __PRELOADED_STATE__ (JSON embebido); si falla, usa JSON-LD o regex de HTML.
- Dependencias: usa curl_cffi si está disponible; si no, cae a requests.
"""

from __future__ import annotations
import re, json, html, os
import urllib.parse as _url
import codecs

# ---- HTTP backend (curl_cffi si existe, sino requests) ----
try:
    from curl_cffi import requests as http
    HAVE_CURL = True
except Exception:
    import requests as http
    HAVE_CURL = False

COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = int(os.getenv("COTO_SUCURSAL_ID", "200"))
DEFAULT_TIMEOUT = float(os.getenv("COTO_TIMEOUT", "8.0"))  # rápido

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

def _build_session():
    if HAVE_CURL:
        s = http.Session(impersonate="chrome", timeout=DEFAULT_TIMEOUT)
    else:
        s = http.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es-419;q=0.9,es;q=0.8,en-US;q=0.7,en;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        # Hints similares a navegación real
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Referer": f"{COTO_BASE}/sitios/cdigi/busqueda?idSucursal={COTO_SUCURSAL_ID}&Dy=1",
    })
    # Cookie de sucursal (reduce bloqueos)
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="www.cotodigital.com.ar")
    s.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain="cotodigital.com.ar")
    return s

def _build_search_url(q: str, nrpp: int = 12) -> str:
    params = {
        "Ntt": q,
        "idSucursal": str(COTO_SUCURSAL_ID),
        "Dy": "1",
        "Nrpp": str(nrpp),  # pocos ítems para bajar poco HTML
    }
    return f"{COTO_BASE}/sitios/cdigi/busqueda?{_url.urlencode(params)}"

# ---------- Parsers: devolvemos SOLO títulos ----------

_PRELOADED_RE = re.compile(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;<", re.S)
_LDJSON_RE    = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S|re.I)
_CARD_TITLE_RE = re.compile(r'title="([^"]+)"')
_H_TAG_RE      = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.S)

_PRELOADED_JSONPARSE_RE = re.compile(r"__PRELOADED_STATE__\s*=\s*JSON\.parse\('(.*?)'\)\s*;?", re.S)
_DISPLAYNAME_RE = re.compile(r'"displayName"\s*:\s*"([^"]+)"')
_GENERIC_NAME_RE = re.compile(r'class="[^"]*(?:product-name|product__name|item-name)[^"]*"[^>]*>(.*?)<', re.S|re.I)
_DATA_NAME_ATTR_RE = re.compile(r'data-name="([^"]+)"', re.I)

def _extract_titles_from_state_obj(data: dict) -> list[str]:
    paths = [
        ["search", "results", "products"],
        ["results", "products"],
        ["search", "products"],
        ["products"],
    ]
    out: list[str] = []
    for path in paths:
        cur = data
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, list):
            for p in cur:
                t = (p or {}).get("displayName") or (p or {}).get("name") or (p or {}).get("title")
                if t:
                    out.append(str(t).strip())
            if out:
                break
    return out

def _extract_preloaded_titles(html_text: str) -> list[str]:
    # 1) window.__PRELOADED_STATE__ = {...};
    m = _PRELOADED_RE.search(html_text)
    if m:
        try:
            raw = html.unescape(m.group(1))
            data = json.loads(raw)
            titles = _extract_titles_from_state_obj(data)
            if titles:
                return titles
        except Exception:
            pass
    # 2) window.__PRELOADED_STATE__ = JSON.parse('...json...');
    m2 = _PRELOADED_JSONPARSE_RE.search(html_text)
    if m2:
        try:
            raw = m2.group(1)
            # des-escape de JSON.parse string literal
            raw = raw.encode("utf-8").decode("unicode_escape")
            data = json.loads(raw)
            titles = _extract_titles_from_state_obj(data)
            if titles:
                return titles
        except Exception:
            pass
    # 3) fallback: rastrillar displayName plano en el HTML
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
                    if name:
                        out.append(str(name))
    elif t in ("Product", "Offer"):
        name = obj.get("name") or obj.get("title")
        if name:
            out.append(str(name))

def _extract_html_titles_fallback(html_text: str, limit: int) -> list[str]:
    titles: list[str] = []
    # 1) atributos title=""
    for m in _CARD_TITLE_RE.finditer(html_text):
        t = m.group(1)
        if t:
            titles.append(t.strip())
            if len(titles) >= limit:
                return titles
    # 2) etiquetas hN
    for m in _H_TAG_RE.finditer(html_text):
        t = re.sub(r"<[^>]+>", " ", m.group(1) or "")
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            titles.append(t)
            if len(titles) >= limit:
                return titles
    # 3) nombres genéricos por clase
    for m in _GENERIC_NAME_RE.finditer(html_text):
        t = re.sub(r"<[^>]+>", " ", m.group(1) or "")
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            titles.append(t)
            if len(titles) >= limit:
                return titles
    # 4) data-name=""
    for m in _DATA_NAME_ATTR_RE.finditer(html_text):
        t = m.group(1)
        if t:
            titles.append(t.strip())
            if len(titles) >= limit:
                return titles
    # 5) último recurso: buscar "name":"..." aislado (peligro de falsos positivos)
    naive = re.findall(r'"name"\s*:\s*"([^"]+)"', html_text)[:limit*2]
    for t in naive:
        t = t.strip()
        if t and t not in titles:
            titles.append(t)
            if len(titles) >= limit:
                break
    return titles[:limit]

# ---------- API pública ----------

def search_coto_requests(query: str, limit: int = 10, nrpp: int = 12) -> list[str]:
    """Devuelve SOLO los títulos de productos para la búsqueda dada (lista[str])."""
    # Si el sitio devuelve un captcha o 'Access Denied', retorna lista vacía.
    s = _build_session()
    url = _build_search_url(query, nrpp=nrpp)
    try:
        r = s.get(url, timeout=DEFAULT_TIMEOUT)
        status = getattr(r, "status_code", None) or getattr(r, "status", 0)
        text = r.text if hasattr(r, "text") else (r.content.decode("utf-8", "ignore"))
    except Exception:
        return []

    body = text or ""
    # bloqueado con 200
    if ("Access Denied" in body) or ("captcha" in body.lower()):
        return []

    # 1) preloaded state
    titles = _extract_preloaded_titles(body)
    if titles:
        return titles[:limit]

    # 2) JSON-LD
    titles = _extract_ldjson_titles(body)
    if titles:
        return titles[:limit]

    # 3) HTML fallback
    titles = _extract_html_titles_fallback(body, limit=limit)
    return titles[:limit]


__all__ = ["search_coto_requests"]