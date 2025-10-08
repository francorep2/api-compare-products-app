# scrapers/coto_scraper_requests.py
from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import requests, re, urllib.parse, json, unicodedata, concurrent.futures, os

# ========= Parser fallback (lxml -> html5lib -> html.parser) =========
try:
    import lxml  # noqa
    BS_PARSER = "lxml"
except Exception:
    try:
        import html5lib  # noqa
        BS_PARSER = "html5lib"
    except Exception:
        BS_PARSER = "html.parser"

from bs4 import BeautifulSoup

# ===================== Config =====================
TOP_N = 3
HOSTS = [
    "https://www.cotodigital.com.ar",
    "https://www.cotodigital3.com.ar",
]
CDIGI_PATH = "/sitios/cdigi"
STATIC_HOSTS = [
    "https://static.cotodigital3.com.ar",
    "https://static.cotodigital.com.ar",
]
COTO_SUCURSAL_ID = 200
REQUEST_TIMEOUT = 7.0
PDP_WORKERS = 4
DEBUG = os.getenv("COTO_DEBUG", "0") == "1"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
})
for dom in (".cotodigital.com.ar", ".cotodigital3.com.ar"):
    SESSION.cookies.set("idSucursal", str(COTO_SUCURSAL_ID), domain=dom)

_WARMED = False
_LAST_REFERER = None

# ===================== Utils =====================
def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def normalize_price(text: Optional[str]) -> Optional[str]:
    if not text: return None
    t = text.replace("\xa0", " ").strip()
    cand = []
    for ent, dec in re.findall(r'(?:\$|ARS)\s*([0-9]{1,3}(?:[.\s][0-9]{3})+|[0-9]{4,})(?:,([0-9]{2}))?', t, flags=re.I):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{ent_clean}.{dec or '00'}"))
        except: pass
    for ent, dec in re.findall(r'([0-9]{1,3}(?:[.\s][0-9]{3})*|[0-9]{1,4}),([0-9]{2})', t):
        ent_clean = ent.replace(".", "").replace(" ", "")
        try: cand.append(float(f"{int(ent_clean)}.{dec}"))
        except: pass
    for ent in re.findall(r'\b([0-9]{4,})\b', t):
        try: cand.append(float(int(ent)))
        except: pass
    if not cand: return None
    return f"ARS {max(cand):.2f}"

def absolutize(base: str, href: str) -> str:
    if not href: return base
    if href.startswith("http"): return href
    parts = base.split("/", 3)
    path = href if href.startswith("/") else ("/"+href)
    return f"{parts[0]}//{parts[2]}{path}"

def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if 8 <= len(d) <= 14 else None

def choose_barcode(*cands) -> Optional[str]:
    for c in cands:
        d = normalize_barcode(str(c) if c is not None else None)
        if d: return d
    return None

# ===================== URL / imagen =====================
def sanitize_coto_href(href:str) -> str:
    s = urllib.parse.unquote(href or "")
    while "&&" in s: s = s.replace("&&", "&")
    s = s.replace("?&", "?")
    base, query = (s.split("?", 1) + [""])[:2] if "?" in s else (s, "")
    q = urllib.parse.parse_qs(query, keep_blank_values=True)
    q["Dy"] = ["1"]
    q["idSucursal"] = [str(COTO_SUCURSAL_ID)]
    pairs = []
    for k in ["Dy", "idSucursal"]:
        for v in q.get(k, []): pairs.append((k, v))
        q.pop(k, None)
    for k,vs in q.items():
        for v in vs: pairs.append((k,v))
    new_query = urllib.parse.urlencode(pairs, doseq=True)
    return f"{base}?{new_query}"

def sku_from_url(url: str) -> Optional[str]:
    m = re.search(r"/R-([0-9]{8})-", url)
    return m.group(1) if m else None

def folder_from_sku(sku: str) -> Optional[str]:
    if not sku or len(sku) != 8: return None
    return sku[:-2] + "00"

def build_static_image_url(sku: str) -> Optional[str]:
    if not sku: return None
    folder = folder_from_sku(sku)
    if not folder: return None
    return f"{STATIC_HOSTS[0]}/sitios/fotos/large/{folder}/{sku}.jpg"

# ===================== Warm-up (cookies + referer) =====================
def _ensure_session_ready():
    global _WARMED, _LAST_REFERER
    if _WARMED:
        return
    for host in HOSTS:
        url = host + CDIGI_PATH + "/nuevositio"
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if 200 <= r.status_code < 400:
                _LAST_REFERER = url
                _WARMED = True
                break
        except Exception:
            continue
    if not _WARMED:
        # igual seguimos, pero sin referer “real”
        _LAST_REFERER = HOSTS[0] + CDIGI_PATH + "/nuevositio"

# ===================== Fetch =====================
def _fetch(url: str, referer: Optional[str] = None) -> Optional[str]:
    try:
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            if DEBUG: print(f"[COTO] fetch {url} -> {r.status_code}")
            return None
        return r.text
    except Exception as e:
        if DEBUG: print(f"[COTO] fetch exc {url}: {e}")
        return None

# ===================== Variantes de búsqueda =====================
def _candidate_search_urls(q: str) -> List[str]:
    qenc = urllib.parse.quote(q)
    urls = []
    for host in HOSTS:
        base = host + CDIGI_PATH + "/categoria"
        # tu URL exacta primero
        urls.append(f"{base}?_dyncharset=utf-8&Dy=1&Ntt={qenc}&idSucursal={COTO_SUCURSAL_ID}")
        # variantes típicas de Oracle Commerce
        urls.append(f"{base}?Dy=1&Ntt={qenc}&N=0&idSucursal={COTO_SUCURSAL_ID}")
        urls.append(f"{base}?Dy=1&Ntt={qenc}&Ntk=All&idSucursal={COTO_SUCURSAL_ID}")
        urls.append(f"{base}?Ntt={qenc}&Ntk=All&N=0&idSucursal={COTO_SUCURSAL_ID}")
    # dedup
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

# ===================== Parse grilla =====================
def _extract_cards_regex(html: str, host: str) -> List[Tuple[str, str, str]]:
    out = []
    # anchors con comillas dobles o simples
    anchors = re.findall(r'href=[\'"]([^\'"]*/sitios/cdigi/productos/[^\'"]+)[\'"][^>]*>(.*?)</a>', html, flags=re.I|re.S)
    seen = set()
    for href, inner in anchors:
        if href in seen: continue
        seen.add(href)
        title = clean_text(re.sub("<.*?>", " ", inner))
        # precio cerca del anchor
        snippet = html.split(href, 1)[-1][:700]
        m = re.search(r'(?:\$|ARS)\s*[0-9\.\s]{1,}(?:,[0-9]{2})?', snippet)
        price_text = m.group(0) if m else ""
        out.append((absolutize(host, href), title or "", price_text))
    return out

def _extract_cards_soup(html: str, host: str) -> List[Tuple[str, str, str]]:
    soup = BeautifulSoup(html, BS_PARSER)
    triples = []
    for a in soup.select("a[href*='/sitios/cdigi/productos/']")[:160]:
        href = a.get("href") or ""; 
        if not href: continue
        inner = a.get_text(" ", strip=True) or ""
        price_text = ""
        node = a
        # probar selectores comunes
        for sel in (".price", ".best-price", ".skuBestPrice", ".new-price"):
            el = node.select_one(sel)
            if not el and node.parent: el = node.parent.select_one(sel)
            if el:
                txt = el.get_text(" ", strip=True)
                if "$" in txt or "ARS" in txt:
                    price_text = txt; break
        if not price_text:
            near = a.parent or a
            txt = near.get_text(" ", strip=True)
            m = re.search(r'(?:\$|ARS)\s*[0-9\.\s]{1,}(?:,[0-9]{2})?', txt)
            price_text = m.group(0) if m else ""
        triples.append((absolutize(host, href), inner, price_text))
    # fallback histórico ul#products
    ul = soup.find("ul", id="products")
    if ul:
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            href = a["href"] if a else ""
            if not href: continue
            desc = li.get_text(" ", strip=True)
            price_el = li.select_one(".atg_store_newPrice")
            price_txt = price_el.get_text(" ", strip=True) if price_el else ""
            triples.append((absolutize(host, href), desc, price_txt))
    return triples

def _parse_search_page(html: str, host: str) -> List[Dict]:
    triples = _extract_cards_regex(html, host)
    if not triples:
        triples = _extract_cards_soup(html, host)
    items: List[Dict] = []
    seen = set()
    for href, title, price_text in triples:
        url = sanitize_coto_href(href)
        if url in seen: continue
        seen.add(url)
        price = normalize_price(price_text)
        sku = sku_from_url(url)
        image_url = build_static_image_url(sku) if sku else None
        items.append({
            "title": title or None,
            "price": price,
            "url": url,
            "image_url": image_url,
            "barcode": None,
        })
    return items

# ===================== JSON-LD (enriquecimiento PDP) =====================
_JSONLD_BLOCK_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S|re.I)
def parse_jsonld_from_text(html: str) -> Optional[Dict]:
    for raw in _JSONLD_BLOCK_RE.findall(html or ""):
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and ("Product" in str(it.get("@type"))):
                name = it.get("name")
                offers = it.get("offers")
                price = None
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    p = offers.get("price")
                    if p:
                        try: price = float(str(p).replace(",", "."))
                        except: pass
                image = it.get("image")
                if isinstance(image, list) and image:
                    image = image[0]
                gtin = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                return {
                    "title": clean_text(name) if name else None,
                    "price": f"ARS {price:.2f}" if price is not None else None,
                    "image": image or None,
                    "barcode": gtin or None,
                }
    return None

def _enrich_from_pdp_jsonld(url: str) -> Optional[Dict]:
    html = _fetch(url, referer=_LAST_REFERER)
    if not html:
        return None
    return parse_jsonld_from_text(html)

# ===================== Ranking =====================
def _rank_and_cut(items: List[Dict], q: Optional[str], limit: int) -> List[Dict]:
    if not items: return []
    if q:
        scored = []
        qs = _norm_text(q)
        for it in items:
            t = _norm_text(it.get("title") or "")
            score = 0
            if qs and t:
                if qs in t: score += 3
                score += sum(1 for w in qs.split() if w in t)
            if it.get("price"): score += 1
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [it for _, it in scored]
    out, seen = [], set()
    for it in items:
        u = it.get("url")
        if not u or u in seen: continue
        seen.add(u)
        out.append(it)
        if len(out) == limit: break
    return out

# ===================== Búsqueda pública =====================
def _search_once(q: str) -> List[Dict]:
    _ensure_session_ready()
    for url in _candidate_search_urls(q):
        host = url.split("/", 3)[0] + "//" + url.split("/", 3)[2]
        html = _fetch(url, referer=_LAST_REFERER)
        if not html:
            continue
        items = _parse_search_page(html, host)
        if DEBUG:
            print(f"[COTO] try {url} -> {len(items)} items")
            if not items:
                open("/tmp/coto_empty.html", "w", encoding="utf-8").write(html)
        if items:
            return items
    return []

def coto_search_top_n(q: str, limit: int = TOP_N, need_barcode: bool = False) -> List[Dict]:
    items = _search_once(q)
    if not items: return []
    pre = _rank_and_cut(items, q, max(limit*2, limit))
    if need_barcode or any(not it.get("price") for it in pre[:limit]):
        to_fill = pre[:limit]
        with concurrent.futures.ThreadPoolExecutor(max_workers=PDP_WORKERS) as ex:
            futs = {ex.submit(_enrich_from_pdp_jsonld, it["url"]): it for it in to_fill if it.get("url")}
            for f in concurrent.futures.as_completed(futs):
                it = futs[f]
                jl = None
                try:
                    jl = f.result()
                except Exception:
                    pass
                if not jl: continue
                it["barcode"] = jl.get("barcode") or it.get("barcode")
                it["price"] = it.get("price") or jl.get("price")
                it["title"] = it.get("title") or jl.get("title")
                img = jl.get("image")
                if img and "cotodigital" in img:
                    it["image_url"] = absolutize(HOSTS[0], img)
    return pre[:limit]

def coto_search_by_barcode(ean: str, limit: int = TOP_N) -> List[Dict]:
    items = _search_once(ean)
    if not items: return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PDP_WORKERS) as ex:
        futs = {ex.submit(_enrich_from_pdp_jsonld, it["url"]): it for it in items[:max(limit*3, 6)]}
        for f in concurrent.futures.as_completed(futs):
            it = futs[f]
            jl = None
            try: jl = f.result()
            except Exception: pass
            if jl:
                it["barcode"] = jl.get("barcode") or it.get("barcode")
                it["price"] = it.get("price") or jl.get("price")
                it["title"] = it.get("title") or jl.get("title")
                img = jl.get("image")
                if img and "cotodigital" in img:
                    it["image_url"] = absolutize(HOSTS[0], img)
    hits = [it for it in items if it.get("barcode") == ean]
    return (hits or items)[:limit]

def search_top_n(query: str, limit: int = TOP_N) -> List[Dict]:
    return coto_search_top_n(query, limit=limit, need_barcode=False)

def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        return coto_search_by_barcode(ean, limit=limit)
    if q and q.strip():
        return coto_search_top_n(q.strip(), limit=limit, need_barcode=False)
    return []
