# scrapers_async/coto_scraper_async.py
from typing import List, Dict, Optional, Tuple
import json, urllib.parse, re, asyncio, datetime
import unicodedata

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200
CONCURRENCY_LIMIT = 4

# ===================== Texto / matching =====================

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q or "")
    if not s:
        return True
    must = []
    # ejemplos de must (ajustables a tu dominio)
    for k in ("cofler","serenisima","oreo","pepitos","lactal","coca","cocacola","quilmes","heineken","pepsi","manaos"):
        if k in s: must.append(k)
    # categorías comunes
    for k in ("galletita","galletitas","cookie","cookies","leche","yogur","yogurt","cerveza","gaseosa","arroz","fideo"):
        if k in s:
            must.append(k)
            break
    # sabores/atributos típicos
    for k in ("chocolate","chips","light","zero","entera","descremada"):
        if k in s: must.append(k)
    ok = sum(1 for m in set(must) if m in t)
    return ok >= max(1, min(3, len(set(must)) - 1))

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
    host = base.split("/", 3)
    path = href if href.startswith("/") else ("/"+href)
    return f"{host[0]}//{host[2]}{path}"

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

# ===================== URL helpers / imagen estática =====================

def sanitize_coto_href(href:str) -> str:
    s = urllib.parse.unquote(href)
    while "&&" in s: s = s.replace("&&", "&")
    s = s.replace("?&", "?")
    if "?" in s:
        base, query = s.split("?", 1)
    else:
        base, query = s, ""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)
    q["Dy"] = ["1"]
    q["idSucursal"] = [str(COTO_SUCURSAL_ID)]
    # preserva orden Dy, idSucursal, luego resto
    pairs = []
    for k in ["Dy", "idSucursal"]:
        for v in q.get(k, []):
            pairs.append((k, v))
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

def build_static_image_url(sku: str, size="large") -> Optional[str]:
    folder = folder_from_sku(sku)
    if not folder: return None
    return f"{COTO_STATIC_HOST}/sitios/fotos/{size}/{folder}/{sku}.jpg"

# ===================== Parsers rápidos (sin render) =====================

_JSONLD_BLOCK_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S|re.I
)

def parse_jsonld_from_text(html: str) -> Optional[Dict]:
    for raw in _JSONLD_BLOCK_RE.findall(html or ""):
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except:
            continue
        stack = data if isinstance(data, list) else [data]
        for it in (stack if isinstance(stack, list) else [stack]):
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

# ===================== Carga/route optimizados =====================

SEARCH_URL_TMPL = (
    f"{COTO_BASE}/sitios/cdigi/categoria?_dyncharset=utf-8&Dy=1&Ntt={{q}}&idSucursal={COTO_SUCURSAL_ID}"
)

async def _configure_blocking(page):
    async def _route(route):
        r = route.request
        rtype = r.resource_type
        url = r.url
        # ⚠️ NO bloquear xhr/fetch (la grilla puede depender de eso)
        if rtype in {"image", "stylesheet", "font", "media"}:
            await route.abort()
            return
        if any(k in url for k in (
            "googletagmanager", "tagmanager", "doubleclick", "facebook",
            "hotjar", "analytics", "sentry", "clarity"
        )):
            await route.abort()
            return
        await route.continue_()

    await page.route("**/*", _route)
    # setters sin await
    page.set_default_navigation_timeout(8000)
    page.set_default_timeout(8000)

async def _page_get_search(ctx, q: str):
    page = await ctx.new_page()
    try:
        await _configure_blocking(page)
        url = SEARCH_URL_TMPL.format(q=urllib.parse.quote(q))
        await page.goto(url, wait_until="domcontentloaded", timeout=7000)

        # Esperar anchors si hidrata por XHR
        try:
            await page.wait_for_selector("a[href*='/sitios/cdigi/productos/']", timeout=3500)
        except:
            try:
                await page.wait_for_load_state("networkidle", timeout=1200)
            except:
                pass
        return page
    except:
        await page.close()
        raise

# ===================== Extracción desde la grilla =====================

def _extract_cards_html(html: str) -> List[Tuple[str, str, str]]:
    """
    Devuelve lista de (href, title, price_text) a partir de la grilla.
    Tolerante a cambios de clase: busca anchors a /sitios/cdigi/productos/ y el texto cercano.
    """
    out = []
    anchors = re.findall(
        r'href="([^"]*/sitios/cdigi/productos/[^"]+)"[^>]*>(.*?)</a>',
        html, flags=re.I|re.S
    )
    seen = set()
    for href, inner in anchors:
        if href in seen:
            continue
        seen.add(href)
        title = clean_text(re.sub("<.*?>", " ", inner))
        # Precio cerca del anchor (evitar header/footer)
        post = html.split(href, 1)[-1][:400]
        m = re.search(r'(?:\$|ARS)\s*[0-9\.\s]{1,}(?:,[0-9]{2})?', post)
        price_text = m.group(0) if m else ""
        out.append((href, title or "", price_text))
    return out

async def _extract_cards_from_page(page) -> List[Dict]:
    html = await page.content()
    triples = _extract_cards_html(html)

    # Fallback robusto con locator si el regex no encontró nada
    if not triples:
        anchors = page.locator("a[href*='/sitios/cdigi/productos/']")
        n = await anchors.count()
        for i in range(min(n, 60)):
            a = anchors.nth(i)
            href = await a.get_attribute("href") or ""
            inner = await a.inner_text() or ""
            if not href:
                continue
            # Precio: mirar el contenedor cercano
            price_text = ""
            try:
                for sel in (".price", ".best-price", ".skuBestPrice", ".new-price", "span:has-text('$')"):
                    el = a.locator(":scope").locator("xpath=ancestor-or-self::*[self::a or self::div][1]").locator(sel).first
                    if await el.count():
                        txt = await el.inner_text()
                        if txt and ("$" in txt or "ARS" in txt):
                            price_text = txt
                            break
            except:
                pass
            triples.append((href, inner, price_text))

    items: List[Dict] = []
    for href, title, price_text in triples:
        url = sanitize_coto_href(absolutize(COTO_BASE, href))
        price = normalize_price(price_text)
        sku = sku_from_url(url)
        image_url = build_static_image_url(sku) if sku else None
        items.append({
            "title": clean_text(title) if title else None,
            "price": price,
            "url": url,
            "image_url": image_url,
            "barcode": None,
        })
    return items

# ===================== Sonda rápida JSON-LD por HTTP (sin Page) =====================

async def _probe_pdp_jsonld_http(ctx, url: str, timeout_ms: int = 1000) -> Optional[Dict]:
    """
    ctx: BrowserContext (ej: page.context)
    timeout_ms: en MILISEGUNDOS (no dividir por 1000)
    """
    try:
        r = await ctx.request.get(url, timeout=timeout_ms)
        if not r or r.status != 200:
            return None
        text = await r.text()
        return parse_jsonld_from_text(text)
    except:
        return None

async def _fill_barcodes_fast(ctx, items: List[Dict], limit: int, timeout_ms_each: int = 1000) -> None:
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    async def worker(it):
        async with sem:
            url = it.get("url")
            if not url:
                return
            jl = await _probe_pdp_jsonld_http(ctx, url, timeout_ms_each)
            if not jl:
                return
            it["barcode"] = jl.get("barcode") or it.get("barcode")
            it["price"] = it.get("price") or jl.get("price")
            it["title"] = it.get("title") or jl.get("title")
            img = jl.get("image")
            if img and "cotodigital" in img:
                it["image_url"] = absolutize(COTO_BASE, img)
    tasks = [asyncio.create_task(worker(it)) for it in items[:limit]]
    await asyncio.gather(*tasks, return_exceptions=True)

# ===================== Ranking y corte =====================

def _rank_and_cut(items: List[Dict], q: Optional[str], limit: int) -> List[Dict]:
    if not items:
        return []
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
        items = [it for it in items if _title_seems_related(it.get("title") or "", q)]
    # dedup por URL
    seen, out = set(), []
    for it in items:
        u = it.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(it)
        if len(out) == limit:
            break
    return out

# ===================== API pública =====================

async def coto_search_top_n(ctx, query: str, limit: int = TOP_N, need_barcode: bool = False) -> List[Dict]:
    page = await _page_get_search(ctx, query)
    try:
        items = await _extract_cards_from_page(page)

        # 🔁 reintento corto si vino vacío
        if not items:
            try:
                await page.wait_for_selector("a[href*='/sitios/cdigi/productos/']", timeout=1000)
            except:
                pass
            items = await _extract_cards_from_page(page)

        if not items:
            return []

        pre = _rank_and_cut(items, query, max(limit*2, limit))
        # Sonda JSON-LD sólo si hace falta (barcode o falta price/title)
        if need_barcode or any(not it.get("price") for it in pre[:limit]):
            await _fill_barcodes_fast(page.context, pre, limit=limit, timeout_ms_each=1000)
        return pre[:limit]
    finally:
        await page.close()

async def coto_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    # Buscar por EAN; validar con sondas HTTP paralelas
    page = await _page_get_search(ctx, ean)
    try:
        items = await _extract_cards_from_page(page)

        if not items:
            try:
                await page.wait_for_selector("a[href*='/sitios/cdigi/productos/']", timeout=1000)
            except:
                pass
            items = await _extract_cards_from_page(page)

        if not items:
            return []

        await _fill_barcodes_fast(page.context, items, limit=max(limit*3, 6), timeout_ms_each=1200)
        hits = [it for it in items if it.get("barcode") == ean]
        return (hits or items)[:limit]
    finally:
        await page.close()

async def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    # Camino más veloz si no necesitás EAN
    return await coto_search_top_n(ctx, query, limit=limit, need_barcode=False)

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        return await coto_search_by_barcode(ctx, ean, limit=limit)
    if q and q.strip():
        return await coto_search_top_n(ctx, q.strip(), limit=limit, need_barcode=False)
    return []
# ===================== Fin del scraper =====================