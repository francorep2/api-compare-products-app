# scrapers/barcode_resolver.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re, urllib.parse
from typing import Optional
import httpx

_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}

async def _get_json(url: str, params: dict | None = None) -> list | dict | None:
    async with httpx.AsyncClient(timeout=6.0, headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url, params=params or {})
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None

async def _get_text(url: str, params: dict | None = None) -> Optional[str]:
    async with httpx.AsyncClient(timeout=6.0, headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url, params=params or {})
        if r.status_code != 200:
            return None
        return r.text

def _pick_name_from_vtex_product(prod: dict) -> Optional[str]:
    for k in ("productTitle", "productName", "productReference", "titleTag"):
        v = prod.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    items = prod.get("items") or []
    if items and isinstance(items, list):
        it = items[0] or {}
        for k in ("nameComplete", "name", "complementName"):
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    brand = prod.get("brand")
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    return None

def _pick_name_from_ldjson(html: str) -> Optional[str]:
    if not html:
        return None
    blocks = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.I | re.S)
    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        arr = data if isinstance(data, list) else [data]
        for d in arr:
            if isinstance(d, dict):
                if d.get("@type") == "Product":
                    name = d.get("name")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
                name = d.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.I | re.S)
    if m:
        txt = re.sub("<[^<]+?>", " ", m.group(1))
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            return txt
    return None

async def _resolve_name_vtex(domain: str, ean: str) -> Optional[str]:
    base = f"https://{domain}"
    api = f"{base}/api/catalog_system/pub/products/search/"
    for fk in ("alternateIds_Ean", "EAN", "alternateIdsRefId"):
        js = await _get_json(api, params={"fq": f"{fk}:{ean}"})
        if isinstance(js, list) and js:
            name = _pick_name_from_vtex_product(js[0])
            if name:
                return name
    js = await _get_json(api, params={"ft": ean, "_from": 0, "_to": 1})
    if isinstance(js, list) and js:
        name = _pick_name_from_vtex_product(js[0])
        if name:
            return name
    path = f"{api}{urllib.parse.quote(ean)}"
    js = await _get_json(path)
    if isinstance(js, list) and js:
        name = _pick_name_from_vtex_product(js[0])
        if name:
            return name
    plp = f"{base}/{urllib.parse.quote(ean)}"
    html = await _get_text(plp, params={"_q": ean, "map": "ft"})
    name = _pick_name_from_ldjson(html or "")
    if name:
        return name
    return None

async def resolve_name_carrefour(ean: str) -> Optional[str]:
    return await _resolve_name_vtex("www.carrefour.com.ar", ean)

async def resolve_name_dia(ean: str) -> Optional[str]:
    return await _resolve_name_vtex("diaonline.supermercadosdia.com.ar", ean)

async def resolve_name_jumbo(ean: str) -> Optional[str]:
    return await _resolve_name_vtex("www.jumbo.com.ar", ean)
