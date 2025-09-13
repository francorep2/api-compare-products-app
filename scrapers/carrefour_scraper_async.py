# scrapers_async/carrefour_scraper_async.py
from typing import List, Dict, Optional, Any
import json, urllib.parse, re, os, datetime, asyncio

# ===================== Config =====================
TOP_N = 3
CARREFOUR_BASE = "https://www.carrefour.com.ar"  # AR (VTEX)

# ===================== Query helpers =====================
def _query_terms(q: str) -> list[str]:
    if not q: return []
    return [t for t in re.findall(r"[A-Za-z0-9]{3,}", q.lower())]

def _title_matches(q_terms: list[str], title: str | None) -> bool:
    if not title or not q_terms:
        return True
    tl = title.lower()
    return any(t in tl for t in q_terms)

def _search_url(query: str) -> str:
    # VTEX: /busca?ft=<query>
    return f"{CARREFOUR_BASE}/busca?ft={urllib.parse.quote(query)}"

async def _try_close_banners(page):
    # Cierra banners de cookies/geo (OneTrust, etc.)
    selectors = [
        "[id*=onetrust-accept]", "#onetrust-accept-btn-handler",
        "button:has-text('Aceptar')", "button:has-text('Entendido')",
        "button:has-text('Continuar')", "[data-testid*=accept]"
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=1000)
                await asyncio.sleep(0.2)
        except:
            pass

# ===================== Utils =====================
def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

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

# ===================== Parsers =====================
async def parse_jsonld_product(page):
    scripts = page.locator("script[type='application/ld+json']")
    count = await scripts.count()
    for i in range(count):
        try:
            raw = (await scripts.nth(i).inner_text()).strip()
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict) and ("Product" in str(it.get("@type"))):
                    name = it.get("name")
                    offers = it.get("offers")
                    price = None
                    if isinstance(offers, list) and offers: offers = offers[0]
                    if isinstance(offers, dict):
                        p = offers.get("price")
                        if p:
                            try: price = float(str(p).replace(",", "."))
                            except: price = None
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
        except:
            pass
    return None

async def parse_meta_price_dom(page) -> Optional[str]:
    for sel in ["meta[itemprop='price']", "meta[property='product:price:amount']"]:
        el = page.locator(sel).first
        if await el.count():
            val = await el.get_attribute("content")
            if val:
                try: return f"ARS {float(str(val).replace(',','.')):.2f}"
                except: pass
    for cont_sel in ["#skuBestPrice", ".skuBestPrice", ".best-price", ".bestPrice", ".price", ".new-price"]:
        cont = page.locator(cont_sel).first
        if not await cont.count():
            continue
        val_el = cont.locator(".val").first
        dec_el = cont.locator(".dec").first
        if await val_el.count():
            entero = (await val_el.inner_text() or "").strip()
            dec = (await dec_el.inner_text() or "").strip() if await dec_el.count() else "00"
            pr = normalize_price(f"$ {entero},{dec}")
            if pr: return pr
        pr = normalize_price(clean_text(await cont.inner_text()))
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
    except: pass
    return None

async def parse_regex_from_html(page) -> Optional[str]:
    html = await page.content()
    m = re.findall(r"(?:\$|ARS)\s*[\d\. ]{1,}(?:,\d{2})?", html, flags=re.I)
    best = None
    for s in m:
        pr = normalize_price(s)
        if pr:
            val = float(pr.replace("ARS","").replace("$","").strip())
            if not best or val > best[0]:
                best = (val, pr)
    return best[1] if best else None

async def extract_image_url(page) -> Optional[str]:
    # JSON-LD primero
    try:
        jl = await parse_jsonld_product(page)
        if jl and jl.get("image"):
            return absolutize(CARREFOUR_BASE, jl["image"])
    except: pass
    # metas OG
    for sel in ["meta[property='og:image']", "meta[name='twitter:image']", "meta[property='og:image:url']"]:
        el = page.locator(sel).first
        if await el.count():
            src = await el.get_attribute("content")
            if src:
                return absolutize(CARREFOUR_BASE, src)
    # tag link
    el = page.locator("link[rel='image_src']").first
    if await el.count():
        src = await el.get_attribute("href")
        if src:
            return absolutize(CARREFOUR_BASE, src)
    # fallback: primera img
    for sel in ["img[itemprop='image']", ".product-image img", ".image-gallery img", "img"]:
        el = page.locator(sel).first
        if await el.count():
            for attr in ["src", "data-src", "data-lazy", "data-original"]:
                src = await el.get_attribute(attr)
                if src:
                    return absolutize(CARREFOUR_BASE, src)
    return None

async def extract_barcode_from_page(page) -> Optional[str]:
    # dataLayer
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
    except: pass
    # JSON-LD crudo
    try:
        html = await page.content()
        for raw in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, flags=re.S|re.I):
            try: data = json.loads(raw)
            except: continue
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
    except: pass
    # Regex genérico
    try:
        txt = await page.content()
        nums = re.findall(r'\b(\d{8,14})\b', txt)
        for pref in (14, 13, 12, 8):
            for n in nums:
                if len(n) == pref:
                    d = normalize_barcode(n)
                    if d: return d
    except: pass
    return None

# ===================== Promos (Carrefour VTEX) =====================
# Soporta: 3x2, 3×2, 2da al 70%, 50% OFF, etc.
PROMO_NORMALIZER = re.compile(
    r"""(?:
        \b(\d{1,3}\s*%?\s*(?:off|dto|descuento))\b |
        \b(\d\s*[\*xX×]\s*\d)\b |
        \b(2da?\s+al\s+\d{1,3}\s*%)\b |
        \b(segund[ao]\s+al\s+\d{1,3}\s*%)\b |
        \b(llevando\s+\d+)\b |
        \b(oferta)\b
    )""",
    re.IGNORECASE | re.VERBOSE
)

def _pick_best_promo_text(text: str) -> Optional[str]:
    if not text:
        return None
    txt = re.sub(r"\s+", " ", text).replace("\xa0", " ").strip()
    m = PROMO_NORMALIZER.search(txt)
    if not m:
        return None
    hit = m.group(0).upper().strip()
    hit = hit.replace("DESCUENTO", "DTO").replace("%OFF", "% OFF").replace("  ", " ")
    hit = hit.replace("×", "x").replace("*", "x")  # normalizar multiplicador
    return hit

VTEX_PROMO_BADGE_SELECTORS = [
    "[class*='flagName']", "[class*='flagText']",
    "[class*='productHighlightText']",
    "[class*='discountBadge']", "[class*='discount']",
    "[data-testid*='discount']", "[data-testid*='badge']",
    ".badge", ".flags", ".tag", ".promo", ".promotion", ".product-flag",
    ".vtex-product-highlights-2-x-productHighlightText",
    ".vtex-store-components-3-x-discountInsideContainer",
    ".vtex-store-components-3-x-productBrandDiscount",
    ".vtex-product-price-1-x-listPriceValue--hasListPrice",
]

async def _extract_promos_from_vtex_state(page) -> Optional[str]:
    """Lee Teasers desde window.__STATE__ cuando existe."""
    try:
        has_state = await page.evaluate("() => typeof window.__STATE__ === 'object' && window.__STATE__ !== null")
        if not has_state:
            return None
        teasers_lists = await page.evaluate("""
            () => {
              const out = [];
              try {
                const S = window.__STATE__ || {};
                for (const k of Object.keys(S)) {
                  const v = S[k];
                  if (v && typeof v === 'object') {
                    let t = null;
                    if (v.commertialOffer && Array.isArray(v.commertialOffer.Teasers)) {
                      t = v.commertialOffer.Teasers;
                    } else if (Array.isArray(v.sellers)) {
                      const s0 = v.sellers[0];
                      if (s0 && s0.commertialOffer && Array.isArray(s0.commertialOffer.Teasers)) {
                        t = s0.commertialOffer.Teasers;
                      }
                    }
                    if (t) out.push(t);
                  }
                }
              } catch(e) {}
              return out;
            }
        """)
        names: list[str] = []
        for arr in (teasers_lists or []):
            for t in (arr or []):
                if isinstance(t, dict):
                    nm = t.get("Name") or t.get("name")
                    if nm:
                        best = _pick_best_promo_text(nm) or nm
                        if best not in names:
                            names.append(best)
        if names:
            return " | ".join(names)
    except:
        pass
    return None

async def extract_promotion_from_dom_vtex(page) -> Optional[str]:
    """
    Orden de preferencia:
      1) window.__STATE__ (Teasers)
      2) skuJson.skus[].sellers[].commertialOffer.Teasers
      3) badges en DOM
      4) patrón en body
    """
    s = await _extract_promos_from_vtex_state(page)
    if s:
        return s

    # skuJson
    try:
        has = await page.evaluate("""
        () => {
          try {
            if (typeof skuJson === 'undefined') return false;
            const s = (skuJson.skus||[])[0];
            if (!s) return false;
            const sell = (s.sellers||[])[0];
            if (!sell || !sell.commertialOffer) return false;
            return Array.isArray(sell.commertialOffer.Teasers) && sell.commertialOffer.Teasers.length>0;
          } catch(e){ return false; }
        }
        """)
        if has:
            teasers = await page.evaluate("""
            () => {
              try {
                const s = (skuJson.skus||[])[0];
                const sell = (s.sellers||[])[0];
                return (sell.commertialOffer?.Teasers||[]).map(t => t && (t.Name || t.name)).filter(Boolean);
              } catch(e){ return []; }
            }
            """)
            if teasers and isinstance(teasers, list):
                cleaned = []
                for t in teasers:
                    best = _pick_best_promo_text(t) or t
                    if best and best not in cleaned:
                        cleaned.append(best)
                if cleaned:
                    return " | ".join(cleaned)
    except:
        pass

    # Badges DOM
    try:
        for sel in VTEX_PROMO_BADGE_SELECTORS:
            loc = page.locator(sel)
            if await loc.count():
                texts = [t.strip() for t in await loc.all_inner_texts() if t.strip()]
                if texts:
                    joined = " | ".join(dict.fromkeys(texts))
                    best = _pick_best_promo_text(joined)
                    if best:
                        return best
    except:
        pass

    # Fallback body
    try:
        body_txt = await page.inner_text("body")
        best = _pick_best_promo_text(body_txt)
        if best:
            return best
    except:
        pass
    return None

def _extract_promo_from_teasers(commertial_offer: dict) -> Optional[str]:
    """Para la rama API: normaliza Teasers[].Name -> texto simple."""
    try:
        teasers = (commertial_offer or {}).get("Teasers") or []
        names = [t.get("Name") or t.get("name") for t in teasers if isinstance(t, dict) and (t.get("Name") or t.get("name"))]
        if names:
            cleaned = []
            for n in names:
                best = _pick_best_promo_text(n) or n
                if best and best not in cleaned:
                    cleaned.append(best)
            if cleaned:
                return " | ".join(cleaned)
    except Exception as e:
        print("[LOG] Promo parse error:", e)
    return None

# ===================== VTEX API search =====================
async def _vtex_api_search(ctx, base_url: str, query: str, limit: int) -> List[Dict[str, Any]]:
    """
    Usa VTEX Catalog Search API (canal sc=3).
    Extrae: title, url, image, price, barcode y promotion (Teasers).
    """
    q = urllib.parse.quote(query or "")
    api = f"{base_url}/api/catalog_system/pub/products/search/{q}?sc=3"
    try:
        resp = await ctx.request.get(api, timeout=30000)
        if not getattr(resp, "ok", False):
            return []
        data = await resp.json()
        items: List[Dict[str, Any]] = []
        for p in (data or [])[: max(limit * 4, limit)]:
            title = p.get("productName") or p.get("productTitle")
            link_text = p.get("linkText") or p.get("link") or ""
            url = f"{base_url}/{link_text}/p" if link_text else (p.get("link") or "")

            price: Optional[str] = None
            image_url: Optional[str] = None
            barcode: Optional[str] = None
            promo_text: Optional[str] = None

            for it in (p.get("items") or []):
                # EAN / barcode
                if not barcode:
                    for rid in (it.get("referenceId") or []):
                        val = (rid or {}).get("Value")
                        if val:
                            d = re.sub(r"\D", "", str(val))
                            if 8 <= len(d) <= 14:
                                barcode = d
                                break
                    if not barcode and it.get("ean"):
                        d = re.sub(r"\D","", str(it["ean"]))
                        if 8 <= len(d) <= 14:
                            barcode = d

                # imagen
                if not image_url:
                    imgs = it.get("images") or []
                    if imgs:
                        image_url = imgs[0].get("imageUrl")

                # precio + promo
                for seller in (it.get("sellers") or []):
                    co = (seller.get("commertialOffer") or {})
                    pr = co.get("Price")
                    if isinstance(pr, (float, int)) and pr > 0:
                        price = f"ARS {pr:.2f}"
                    if promo_text is None:
                        promo_text = _extract_promo_from_teasers(co)
                    if price and promo_text:
                        break

                if price and image_url and promo_text and barcode:
                    break

            items.append({
                "title": title,
                "price": price,
                "url": url,
                "image_url": image_url,
                "barcode": barcode,
                "promotion": promo_text,
            })
        return items
    except Exception as e:
        print("[LOG] VTEX API error:", e)
    return []

# ===================== PDP =====================
async def fetch_pdp(ctx, href: str, timeout=60000) -> Dict:
    page = await ctx.new_page()
    try:
        url = absolutize(CARREFOUR_BASE, href)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        # Si nos dejaron en landing de promociones, abortamos
        current = page.url
        if "/promociones" in current:
            print("[LOG] Redirigido a landing de promociones; no es PDP.")
            return {"title": None, "price": None, "url": current, "image_url": None, "barcode": None, "promotion": None}

        await page.wait_for_load_state("networkidle", timeout=timeout)
        for _ in range(2):
            await page.mouse.wheel(0, 800)
            await asyncio.sleep(0.2)

        # título
        title = None
        for sel in ["h1[itemprop='name']", "h1.productName", "h1.product-name", "h1", ".product-name"]:
            el = page.locator(sel).first
            if await el.count():
                t = clean_text(await el.inner_text())
                if t and len(t) > 2:
                    title = t; break

        # precio (JSON-LD -> metas -> DOM -> regex)
        price = None
        jl = await parse_jsonld_product(page)
        if jl:
            price = jl.get("price") or price
            if jl.get("title") and not title: title = jl["title"]
        if not price: price = await parse_meta_price_dom(page)
        if not price: price = await parse_regex_from_html(page)
        if not title: title = clean_text(await page.title())

        image_url = await extract_image_url(page)
        barcode = jl.get("barcode") if jl and jl.get("barcode") else None
        if not barcode: barcode = await extract_barcode_from_page(page)

        # PROMO
        promotion = await extract_promotion_from_dom_vtex(page)

        return {"title": title, "price": price, "url": url,
                "image_url": image_url, "barcode": barcode,
                "promotion": promotion}
    except Exception as e:
        print("[LOG] PDP error Carrefour:", e)
        await save_debug_html(page, "carrefour_pdp_error")
        return {"title": None, "price": None, "url": href, "image_url": None, "barcode": None, "promotion": None}
    finally:
        await page.close()

# ===================== Search (top-N / por EAN) =====================
async def carrefour_search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    base = CARREFOUR_BASE
    q_terms = _query_terms(query)

    # 1) API VTEX primero (más estable, trae Teasers)
    api_items = await _vtex_api_search(ctx, base, query, limit)
    api_items = [it for it in api_items if _title_matches(q_terms, it.get("title"))]
    if api_items:
        return api_items[:limit]

    # 2) Fallback DOM
    page = await ctx.new_page()
    try:
        url = _search_url(query)
        print(f"[LOG] Carrefour search GET {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=50000)
        await _try_close_banners(page)

        try:
            await page.wait_for_selector("a[href*='/p']", timeout=9000)
        except:
            await save_debug_html(page, "carrefour_search_empty_grid")
            return []

        anchors = page.locator("a[href*='/p']:not([href*='promociones']):not([href^='#'])")
        total = await anchors.count()
        results, seen = [], set()
        for i in range(min(total, limit * 8)):
            href = (await anchors.nth(i).get_attribute("href")) or ""
            if not href or "/promociones" in href or href.startswith("#"):
                continue
            pdp = await fetch_pdp(ctx, href)
            if not pdp.get("url") or pdp["url"] in seen:
                continue
            if not _title_matches(q_terms, pdp.get("title")):
                continue
            seen.add(pdp["url"]); results.append(pdp)
            if len(results) >= limit: break

        if not results:
            await save_debug_html(page, "carrefour_search_no_matches")
        return results[:limit]
    except Exception as e:
        print("[LOG] Carrefour search error:", e)
        await save_debug_html(page, "carrefour_search_error")
        return []
    finally:
        await page.close()

async def carrefour_search_by_barcode(ctx, ean: str, limit: int = TOP_N) -> List[Dict]:
    """
    Carrefour VTEX no siempre expone EAN en PDP → estrategia:
    - Buscar por texto usando el EAN
    - Filtrar por el barcode extraído; si ninguno lo trae, devolvemos top-N igual
    """
    prelim = await carrefour_search_top_n(ctx, ean, limit=limit * 4)
    out = []
    for it in prelim:
        bc = it.get("barcode")
        if bc and bc == ean:
            out.append(it)
        if len(out) == limit:
            break
    return out or prelim[:limit]

# ===================== API pública =====================
async def search_top_n(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    return await carrefour_search_top_n(ctx, query, limit=limit)

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    ean = normalize_barcode(barcode or (q or ""))
    if ean and (not q or q.strip() == ean):
        return await carrefour_search_by_barcode(ctx, ean, limit=limit)
    if q and q.strip():
        return await carrefour_search_top_n(ctx, q.strip(), limit=limit)
    return []
