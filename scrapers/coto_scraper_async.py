# scrapers_async/coto_scraper_async.py
from typing import List, Dict, Optional, Any
import json, urllib.parse, re, time, datetime, os, asyncio, unicodedata, contextlib

from app.core.browser_async import browser_manager_async
from scrapers._utils_promos import PROMO_NORMALIZER

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_STATIC_HOST = "https://static.cotodigital3.com.ar"
COTO_SUCURSAL_ID = 200

CONCURRENCY_LIMIT = 2
DEFAULT_TIMEOUT_MS = 5000           # 5s está bien como default
DEFAULT_SEARCH_TIMEOUT_MS = 3000    # <--- BAJAR de 4000 a 3000
PDP_TIMEOUT_MS = 8000

SCRAPER_BUDGET_SEC = 9.0   # deja ~3s al endpoint de 12s
FAST_MODE_DEFAULT = True   # ← clave: sin PDP por defecto

# ===================== Texto / matching =====================

def _norm_text(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s

def _title_seems_related(title: str, q: str) -> bool:
    t = _norm_text(title)
    s = _norm_text(q)
    must = []
    if "cofler" in s: must.append("cofler")
    for k in ("galletita", "galletitas", "cookie", "cookies"):
        if k in s:
            must.append(k)
            break
    for k in ("chocolate", "chips"):
        if k in s: must.append(k)
    ok = sum(1 for m in must if m in t)
    return ok >= max(2, len(must) - 1)

# ===================== Utils comunes (sync) =====================

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
    for ent, dec in re.findall(r'(?:\$|ARS)\s*([0-9]{1,4})\s+([0-9]{2})', t, flags=re.I):
        try: cand.append(float(f"{int(ent)}.{dec}"))
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

async def save_debug_html(page, tag: str):
    try:
        os.makedirs("debug", exist_ok=True)
        html = await page.content()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug", f"{tag}_{ts}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html)
        print(f"[LOG] Guardado HTML: {path}")
    except Exception as e:
        print("[LOG] No pude guardar HTML:", e)

def absolutize(base: str, href: str, current_url: Optional[str] = None) -> str:
    if not href: return current_url or base
    if href.startswith("http"): return href
    parts = base.split("/", 3)
    path = href if href.startswith("/") else ("/"+href)
    return f"{parts[0]}//{parts[2]}{path}"

# Bloqueo de recursos pesados para acelerar cargas
async def _enable_speed_routes(page):
    async def _route_handler(route):
        try:
            if route.request.resource_type in {"image", "font", "media", "stylesheet", "other"}:
                return await route.abort()
        except Exception:
            pass
        await route.continue_()
    await page.route("**/*", _route_handler)
    # Limita a mismo dominio
    await page.route(
        re.compile(r"^(?!https?://(www\.)?cotodigital\.com\.ar/).*$"),
        lambda r: r.abort(),
    )

# Navegación robusta con reintentos
async def _goto_with_retries(page, url: str, wait_until: str = "domcontentloaded",timeout: int = DEFAULT_TIMEOUT_MS, attempts: int = 1):
    last_err = None
    for i in range(attempts):
        try:
            return await page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.4 * (i + 1))
    raise last_err if last_err else RuntimeError("goto failed without exception")

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

def _time_left(deadline: float) -> float:
    import time as _t
    return deadline - _t.monotonic()

async def _compute_discount_from_prices(page) -> Optional[str]:
    try:
        html = await page.content()
        m_reg = re.search(r'Precio\s+regular\s*:\s*\$?\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        m_all = re.findall(r'\$\s*([\d\.\s]+),(\d{2})', html, flags=re.I)
        if m_reg and m_all:
            ent_reg, dec_reg = m_reg.group(1), m_reg.group(2)
            reg = float(ent_reg.replace(".", "").replace(" ", "") + "." + dec_reg)
            candidates = []
            for ent, dec in m_all:
                try:
                    val = float(ent.replace(".", "").replace(" ", "") + "." + dec)
                    candidates.append(val)
                except:
                    pass
            if not candidates:
                return None
            current = min((v for v in candidates if v < reg), default=None)
            if current is None:
                current = min(candidates)
            if reg > 0 and current is not None and current < reg:
                pct = round((reg - current) / reg * 100)
                if 1 <= pct <= 95:
                    return f"{pct}% DTO"
    except Exception:
        pass
    return None

# ===================== Promos (badges) =====================

PROMO_RE = re.compile(
    r"""
    (?P<nxm>\b(?P<n1>\d{1,2})\s*[xX×]\s*(?P<m1>\d{1,2})\b)
    |(?P<llevando>\bllevand[oa]\s+(?P<n2>\d{1,2})\s+(?:pag[aá]s?|paga)\s+(?P<m2>\d{1,2})\b)
    |(?P<precio_de>\b(?P<n3>\d{1,2})\s+al\s+precio\s+de\s+(?P<m3>\d{1,2})\b)
    |(?P<seg_unid>\b(?:2da?|segund[oa])\s+al\s+(?P<pct2>\d{1,3})\s*%?\b)
    |(?P<pct>\b(?P<pct1>\d{1,3})\s*%?\s*(?:off|dto|descuento)\b)
    |(?P<hasta>\bhasta\s+(?P<pcth>\d{1,3})\s*%?\b)
    |(?P<llevando_pct>\bllevand[oa]\s+\d{1,2}.*?(?P<pct3>\d{1,3})\s*%.*?(?:2da?|segund[oa]))
    """,
    re.IGNORECASE | re.VERBOSE
)

GRID_BADGE_SELECTORS = [
    "[class*='flagName']",
    "[class*='flagText']",
    "[class*='productHighlightText']",
    "[class*='discount']",
    "[data-testid*='discount']",
    "[data-testid*='badge']",
    "[data-testid*='price-badge']",
    "[data-testid*='promo']",
    ".badge", ".flags", ".tag", ".promo", ".promotion", ".product-flag",
    "span:has-text('OFERTA')", "span:has-text('Dto')", "strong:has-text('OFERTA')"
]

def _pick_best_promo_text(text: str) -> Optional[str]:
    if not text:
        return None
    txt = re.sub(r"\s+", " ", text).strip()
    m = PROMO_NORMALIZER.search(txt)
    if not m:
        return None
    hit = m.group(0).upper().replace("  ", " ").strip()
    hit = hit.replace("DESCUENTO", "DTO").replace("%OFF", "% OFF")
    return hit

async def extract_promotion_from_dom_page(page) -> Optional[str]:
    COTO_BADGE_SELECTORS = [
        ".promo", ".promotion", ".badge", ".flags", ".product-flag", ".ribbon",
        "[class*='promo']", "[class*='Promotion']", "[class*='badge']",
        ".descuento", ".beneficio", ".ahorro", ".etiqueta", ".label",
        ".tag", ".tag-oferta", ".tag-descuento", ".product__badge",
        ".prod__promo", ".prod__flag", ".product-promotion",
        "span:has-text('OFERTA')", "strong:has-text('OFERTA')", "span:has-text('Dto')",
        "[data-testid*='badge']", "[data-testid*='promo']", "[data-testid*='price-badge']"
    ]
    def _try_pick(txts: list[str]) -> Optional[str]:
        txt = " | ".join(t for t in txts if t)
        m_pct = re.search(r'\b(\d{1,3})\s*%', txt, flags=re.I)
        if m_pct:
            return f"{m_pct.group(1)}% DTO"
        return _pick_best_promo_text(txt)

    for sel in COTO_BADGE_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count():
                texts = [t.strip() for t in await loc.all_inner_texts() if t and t.strip()]
                try:
                    first = loc.first
                    for attr in ("aria-label", "title"):
                        val = await first.get_attribute(attr)
                        if val: texts.append(val.strip())
                except:
                    pass
                promo = _try_pick(texts)
                if promo:
                    return promo
        except:
            continue

    try:
        price_zone = page.locator("xpath=//*[contains(@class,'price') or contains(@class,'precio') or contains(@class,'product-price')][1]")
        if await price_zone.count():
            texts = [t.strip() for t in await price_zone.all_inner_texts() if t and t.strip()]
            promo = _try_pick(texts)
            if promo:
                return promo
    except:
        pass

    calc = await _compute_discount_from_prices(page)
    if calc:
        return calc

    try:
        body_txt = await page.inner_text("body")
        return _try_pick([body_txt])
    except:
        return None

async def extract_promotion_from_card(card_locator) -> Optional[str]:
    try:
        card = card_locator.locator("xpath=ancestor::*[contains(@class,'product') or contains(@class,'summary') or contains(@class,'grid')][1]")
    except:
        card = card_locator
    for sel in GRID_BADGE_SELECTORS:
        try:
            loc = card.locator(sel)
            if await loc.count():
                texts = [t.strip() for t in await loc.all_inner_texts() if t.strip()]
                if texts:
                    compact = " | ".join(dict.fromkeys(texts))
                    best = _pick_best_promo_text(compact)
                    if best:
                        return best
        except:
            continue
    return None

# ===================== Parsers async =====================

async def parse_jsonld_product(page):
    scripts = page.locator("script[type='application/ld+json']")
    count = await scripts.count()

    def _walk(obj):
        stack = [obj]
        while stack:
            it = stack.pop()
            if isinstance(it, dict):
                yield it
                for v in it.values():
                    stack.append(v)
            elif isinstance(it, list):
                for v in it:
                    stack.append(v)

    def _coerce_price(v):
        if v is None: return None
        s = str(v).strip().replace(".", "").replace(",", ".")  # "2.653,33" -> "2653.33"
        try:
            return float(s)
        except:
            return None

    for i in range(count):
        try:
            raw = (await scripts.nth(i).inner_text()).strip()
            data = json.loads(raw)
        except:
            continue

        candidates: List[Any] = []
        if isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        elif isinstance(data, list):
            candidates = data
        else:
            candidates = [data]

        for node in _walk(candidates):
            t = node.get("@type")
            is_product = "Product" in (t if isinstance(t, str) else str(t))
            if not is_product:
                continue

            name = node.get("name") or node.get("headline")
            image = node.get("image")
            if isinstance(image, list) and image:
                image = image[0]

            offers = node.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            price = None
            if isinstance(offers, dict):
                price = _coerce_price(
                    offers.get("price") or
                    (offers.get("priceSpecification") or {}).get("price")
                )

            gtin = choose_barcode(
                node.get("gtin13"),
                node.get("gtin14"),
                node.get("gtin"),
                node.get("sku")
            )

            return {
                "title": clean_text(name) if name else None,
                "price": f"ARS {price:.2f}" if price is not None else None,
                "image": image or None,
                "barcode": gtin or None,
            }

    return None

async def parse_meta_price_dom(page) -> Optional[str]:
    try:
        await asyncio.wait_for(page.wait_for_selector("meta[itemprop='price'], meta[property='product:price:amount']", timeout=300), timeout=0.35)
    except Exception:
        pass
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        el = page.locator(sel).first
        if await el.count():
            val = await el.get_attribute("content")
            if val:
                try: return f"ARS {float(str(val).replace('.','').replace(',','.')):.2f}"
                except: pass
    for cont_sel in ["#skuBestPrice", ".skuBestPrice", ".best-price", ".bestPrice", ".price", ".new-price", "[data-price]", "[data-testid*='price']"]:
        cont = page.locator(cont_sel).first
        if not await cont.count():
            continue
        val = clean_text(await cont.inner_text())
        pr = normalize_price(val)
        if pr: return pr
    for sel in ["span:has-text('$')", "div:has-text('$')"]:
        el = page.locator(sel).first
        if await el.count():
            pr = normalize_price(clean_text(await el.inner_text()))
            if pr: return pr
    try:
        has = await page.evaluate("() => typeof window.skuJson !== 'undefined'")
        if has:
            data = await page.evaluate("() => window.skuJson")
            if isinstance(data, dict):
                for s in (data.get("skus") or []):
                    bp = s.get("bestPrice")
                    if bp: return f"ARS {float(bp)/100:.2f}"
    except:
        pass
    return None

async def parse_regex_from_html(page) -> Optional[str]:
    html = await page.content()
    matches = re.findall(r"(?:\$|ARS)\s*[\d\. ]{1,}(?:,\d{2})?", html, flags=re.I)
    best_val = None
    best_str = None
    for s in matches:
        pr = normalize_price(s)
        if not pr:
            continue
        try:
            val = float(pr.replace("ARS", "").replace("$", "").strip())
        except Exception:
            continue
        if best_val is None or val > best_val:
            best_val = val
            best_str = pr
    return best_str

# ===================== Imagen estática Coto =====================

STATIC_IMG_REGEX = re.compile(
    r"https?://static\.cotodigital(?:3)?\.com\.ar/sitios/fotos/(?:large|medium|small)/[0-9]{8}/[0-9]{8}\.jpg",
    re.IGNORECASE
)

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

async def extract_static_image_from_html(page) -> Optional[str]:
    html = await page.content()
    m = STATIC_IMG_REGEX.search(html)
    return m.group(0) if m else None

async def extract_image_url(page, final_url) -> Optional[str]:
    url = await extract_static_image_from_html(page)
    if url: return url
    sku = sku_from_url(final_url)
    if sku:
        built = build_static_image_url(sku, size="large")
        if built: return built
    try:
        jl = await parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(COTO_BASE, jl["image"])
    except: 
        pass

    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if await el.count():
            src = await el.get_attribute("content")
            if src:
                return absolutize(COTO_BASE, src)

    el = page.locator("link[rel='image_src']").first
    if await el.count():
        src = await el.get_attribute("href")
        if src:
            return absolutize(COTO_BASE, src)

    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if await el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original", "data-srcset"]:
                src = await el.get_attribute(attr)
                if src:
                    src = src.split()[0]
                    return absolutize(COTO_BASE, src)
    return None

# ===================== EAN extractors =====================

async def extract_barcode_from_datalayer_or_html(page) -> Optional[str]:
    try:
        has_dl = await page.evaluate("() => Array.isArray(window.dataLayer)")
        if has_dl:
            arr = await page.evaluate("() => window.dataLayer")
            for it in arr:
                if isinstance(it, dict):
                    for k in ("ean","gtin","gtin13","productEAN","productGtin","codigoBarras","barcode"):
                        val = it.get(k)
                        d = normalize_barcode(val if val is not None else None)
                        if d: return d
    except: 
        pass
    try:
        html = await page.content()
        for raw in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, flags=re.S|re.I):
            with contextlib.suppress(Exception):
                data = json.loads(raw)
            if not data: 
                continue
            stack = [data]
            while stack:
                it = stack.pop()
                if isinstance(it, dict):
                    t = it.get("@type")
                    if "Product" in (t if isinstance(t, str) else str(t)):
                        bc = choose_barcode(it.get("gtin13"), it.get("gtin14"), it.get("gtin"), it.get("sku"))
                        if bc: return bc
                    stack.extend(it.values())
                elif isinstance(it, list):
                    stack.extend(it)
    except: 
        pass
    try:
        txt = await page.content()
        nums = re.findall(r'\b(\d{8,14})\b', txt)
        for pref in (14, 13, 12, 8):
            for n in nums:
                if len(n) == pref:
                    d = normalize_barcode(n)
                    if d: return d
    except: 
        pass
    return None

# ===================== PDP/Search =====================

def sanitize_coto_href(href:str) -> str:
    s = urllib.parse.unquote(href)
    while "&&" in s: s = s.replace("&&", "&")
    s = s.replace("?&", "?")
    if "?" in s:
        base, query = s.split("?", 1)
    else:
        base, query = s, ""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)
    if "Dy" not in q: q["Dy"] = ["1"]
    if "idSucursal" not in q: q["idSucursal"] = [str(COTO_SUCURSAL_ID)]
    pairs = []
    for k in ["Dy", "idSucursal"]:
        if k in q:
            for v in q[k]: pairs.append((k,v))
            del q[k]
    for k,vs in q.items():
        for v in vs: pairs.append((k,v))
    new_query = urllib.parse.urlencode(pairs, doseq=True)
    return f"{base}?{new_query}"

async def fetch_coto_pdp(href, timeout=PDP_TIMEOUT_MS) -> Dict:
    async with browser_manager_async.page() as page:
        await _enable_speed_routes(page)
        try:
            url = absolutize(COTO_BASE, href)
            url = sanitize_coto_href(url)
            print(f"[LOG] PDP GET {url}")

            # Navegación y pequeña ventana para montado React
            await _goto_with_retries(page, url, wait_until="domcontentloaded", timeout=timeout)
            for _ in range(3):
                await asyncio.sleep(0.8)
                body_txt = (await page.inner_text("body"))[:2000]
                if len(body_txt.strip()) >= 200 and "Cargando" not in body_txt:
                    break
                await page.mouse.wheel(0, 900)

            # === Título ===
            title = None
            TITLE_SELECTORS = [
                "[data-testid='product-title']",
                "h1.product__title",
                "h1.product-name",
                "h1",
                "[class*='product'] h1",
                "meta[property='og:title']::attr(content)"
            ]
            for sel in TITLE_SELECTORS:
                if "::attr(" in sel:
                    try:
                        val = await page.evaluate("""sel => {
                            const [s,attr] = sel.split("::attr(");
                            const a = attr.replace(")","");
                            const el = document.querySelector(s);
                            return el ? el.getAttribute(a) : null;
                        }""", sel)
                        if val:
                            title = clean_text(val); break
                    except:
                        pass
                else:
                    el = page.locator(sel).first
                    if await el.count():
                        t = clean_text(await el.inner_text())
                        if t: title = t; break

            # === Precio (JSON-LD / metas / regex) ===
            price = None
            jl = await parse_jsonld_product(page)
            if jl:
                price = jl.get("price") or price
                if jl.get("title") and not title:
                    title = jl["title"]

            if not price:
                PRICE_SELECTORS = [
                    "[data-price]", "[data-testid*='price']",
                    ".price__value", ".price__number", ".product-price",
                    "span.price", "div.price", "span[class*='precio']",
                    "meta[itemprop='price']::attr(content)",
                    "meta[property='product:price:amount']::attr(content)"
                ]
                for sel in PRICE_SELECTORS:
                    if "::attr(" in sel:
                        try:
                            val = await page.evaluate("""sel => {
                                const [s,attr] = sel.split("::attr(");
                                const a = attr.replace(")","");
                                const el = document.querySelector(s);
                                return el ? el.getAttribute(a) : null;
                            }""", sel)
                            if val:
                                try:
                                    f = float(str(val).replace(".", "").replace(",", "."))
                                    price = f"ARS {f:.2f}"; break
                                except:
                                    pr = normalize_price(val)
                                    if pr: price = pr; break
                        except:
                            pass
                    else:
                        el = page.locator(sel).first
                        if await el.count():
                            val = clean_text(await el.inner_text())
                            pr = normalize_price(val)
                            if pr: price = pr; break

            if not price:
                price = await parse_meta_price_dom(page)
            if not price:
                price = await parse_regex_from_html(page)
            if not title:
                title = clean_text(await page.title())

            # === Imagen ===
            image_url = await extract_image_url(page, url)

            # === Código de barras ===
            barcode = None
            if jl and jl.get("barcode"):
                barcode = jl["barcode"]
            if not barcode:
                barcode = await extract_barcode_from_datalayer_or_html(page)

            # === Promoción ===
            promotion = await extract_promotion_from_dom_page(page)

            return {
                "title": title,
                "price": price,
                "url": url,
                "image_url": image_url,
                "barcode": barcode,
                "promotion": promotion,
            }

        except Exception as e:
            print(f"[LOG] PDP error en Coto: {e}")
            await save_debug_html(page, "coto_pdp_error")
            return {
                "title": None,
                "price": None,
                "url": href,
                "image_url": None,
                "barcode": None,
                "promotion": None,
            }

# ===== Helpers extra: extracción rápida desde la grilla =====

async def _extract_card_pairs(page):
    """
    Devuelve lista de (href, card_promo, card_title) desde la grilla.
    """
    anchors_selector = "a[href*='/sitios/cdigi/productos/']"

    # intentar cargar anclas
    got_anchors = False
    try:
        await page.wait_for_selector(anchors_selector, timeout=100)
        got_anchors = True
    except Exception:
        pass
    if not got_anchors:
        try:
            for _ in range(2):
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.15)
            await page.wait_for_selector(anchors_selector, timeout=800)
            got_anchors = True
        except Exception:
            got_anchors = False

    pairs = []

    if got_anchors:
        # JS: mapea card -> href, titulo y promo visible
        data = await page.eval_on_selector_all(
            "a[href*='/sitios/cdigi/productos/']",
            """(as) => as.slice(0, 30).map(a => {
                const href = a.getAttribute('href') || '';
                const card = a.closest('[class*="product"], [class*="summary"], [class*="grid"]') || a.parentElement;
                const pick = (sel) => card?.querySelector(sel);
                const tSel = [
                  "[data-testid='product-title']",
                  "h3", "h2", ".product__title", ".product-name"
                ];
                let title = null;
                for (const s of tSel) { const el = pick(s); if (el && el.textContent) { title = el.textContent.trim(); break; } }

                const pSel = [
                  "[data-testid*='discount']",
                  "[data-testid*='badge']",
                  "[data-testid*='promo']",
                  "[class*='flagName']",
                  "[class*='flagText']",
                  "[class*='productHighlightText']",
                  ".badge", ".promo", ".promotion", ".tag"
                ];
                let promo = null;
                for (const s of pSel) { const el = pick(s); if (el && el.textContent) { promo = el.textContent.trim(); break; } }

                return { href, card_title: title, card_promo: promo };
            })"""
        )
        for d in data or []:
            href = d.get("href") or ""
            pairs.append((href, d.get("card_promo"), d.get("card_title")))
    else:
        # fallback regex
        html = await page.content()
        for h in re.findall(r'href="([^"]+/sitios/cdigi/productos/[^"]+)"', html, flags=re.I):
            pairs.append((h, None, None))

    return pairs

# ===================== Búsqueda =====================
async def coto_search_top_n(query: str, limit: int = TOP_N, fast: bool = FAST_MODE_DEFAULT) -> List[Dict]:
    import time as _t
    deadline = _t.monotonic() + SCRAPER_BUDGET_SEC

    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={urllib.parse.quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )

    async with browser_manager_async.page() as page:
        await _enable_speed_routes(page)
        try:
            await _goto_with_retries(page, search_url, wait_until="domcontentloaded", timeout=DEFAULT_SEARCH_TIMEOUT_MS)
            triplets = await _extract_card_pairs(page)  # [(href, card_promo, card_title)]
        except Exception as e:
            print("[LOG] Classic search error:", e)
            await save_debug_html(page, "coto_classic_error")
            triplets = []

    if not triplets:
        return []

    # Normalizar y deduplicar
    norm: list[tuple[str, Optional[str], Optional[str]]] = []
    seen = set()
    for h, cp, ct in triplets[: max(limit * 4, 12)]:
        try:
            full = sanitize_coto_href(absolutize(COTO_BASE, h))
        except Exception:
            full = h
        if full in seen:
            continue
        seen.add(full)
        norm.append((full, cp, ct))

    # FAST: devolver solo lo que hay en la card (SIN PDP)
    out: List[Dict] = []
    for url, card_promo, card_title in norm:
        out.append({
            "title": clean_text(card_title) if card_title else None,
            "price": None,
            "url": url,
            "image_url": None,
            "barcode": None,
            "promotion": _pick_best_promo_text(card_promo or "") if card_promo else None,
        })
        if len(out) >= limit:
            break

    # Safety: si por algún motivo quedó corto, completa con URLs
    if len(out) < limit:
        for url, cp, ct in norm:
            if any(x["url"] == url for x in out):
                continue
            out.append({
                "title": clean_text(ct) if ct else None,
                "price": None,
                "url": url,
                "image_url": None,
                "barcode": None,
                "promotion": _pick_best_promo_text(cp or "") if cp else None,
            })
            if len(out) >= limit:
                break

    return out[:limit]


# ===================== Búsqueda por barcode =====================

async def coto_search_by_barcode(ean: str, limit: int = TOP_N) -> List[Dict]:
    prelim = await coto_search_top_n(ean, limit=limit*4)
    out = []
    for item in prelim:
        bc = item.get("barcode")
        if bc and str(bc) == ean:
            out.append(item)
        if len(out) == limit:
            break
    return out or prelim[:limit]

async def search_top_n(query: str, limit: int = TOP_N) -> List[Dict]:
    return await coto_search_top_n(query, limit=limit, fast=True)

async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        # Búsqueda por EAN (rápida)
        return await coto_search_top_n(ean, limit=limit, fast=True)
    if q and q.strip():
        return await coto_search_top_n(q.strip(), limit=limit, fast=True)
    return []
