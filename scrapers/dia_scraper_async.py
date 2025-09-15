# scrapers/dia_scraper_async.py
import re
import json
from typing import List, Dict, Optional
from app.models.search_item import SearchItem

# --- Patrones ---
PROMO_PAT = re.compile(r"(\d{1,3}\s?%|\b\d+\s*[x×]\s*\d+\b)", re.IGNORECASE)
BARCODE_PAT = re.compile(r"\b(\d{8,14})\b")  # ean8/12/13/14

# ---------------------------
# Helpers de BARCODE (PDP)
# ---------------------------
def _only_digits(s: str) -> Optional[str]:
    d = re.sub(r"\D", "", s or "")
    return d if d else None

async def _barcode_from_visible(page) -> Optional[str]:
    """
    Lee el EAN visible en el PDP:
    <span class="vtex-product-identifier-0-x-product-identifier__value">7790990002834</span>
    """
    for sel in [
        ".vtex-product-identifier-0-x-product-identifier__value",
        "[class*='product-identifier__value']",
        ".vtex-product-identifier-0-x-product-identifier",
    ]:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            txt = (await el.inner_text()).strip()
            d = _only_digits(txt)
            # aceptamos 12/13/14 (UPC/EAN/ITF)
            if d and 12 <= len(d) <= 14:
                return d
        except:
            pass
    return None

async def _barcode_from_ld_json(page) -> Optional[str]:
    """
    Busca gtin/sku en <script type="application/ld+json"> del PDP.
    """
    try:
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for s in scripts:
            raw = (await s.inner_text()).strip()
            objs = []
            try:
                objs.append(json.loads(raw))
            except json.JSONDecodeError:
                # por si embeben varios JSON en el mismo script
                for m in re.finditer(r"{.*?}", raw, flags=re.DOTALL):
                    try:
                        objs.append(json.loads(m.group(0)))
                    except:
                        pass
            for obj in objs:
                for o in (obj if isinstance(obj, list) else [obj]):
                    if not isinstance(o, dict):
                        continue
                    if (o.get("@type") or "").lower() == "product":
                        for k in ("gtin13", "gtin", "gtin12", "sku"):
                            v = o.get(k)
                            d = _only_digits(str(v)) if v else None
                            if d and 12 <= len(d) <= 14:
                                return d
    except:
        pass
    return None

async def _barcode_from_vtex_state(page) -> Optional[str]:
    """
    Recorre window.__STATE__ (Apollo cache de VTEX) buscando ean/gtin/barcode/referenceId.
    """
    try:
        st_json = await page.evaluate(
            "() => { try { return JSON.stringify(window.__STATE__||null) } catch(e){ return null } }"
        )
        if not st_json:
            return None
        st = json.loads(st_json)
        found = None

        def walk(o):
            nonlocal found
            if found:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    lk = str(k).lower()
                    if any(x in lk for x in ["ean", "gtin", "barcode"]):
                        d = _only_digits(str(v))
                        if d and 12 <= len(d) <= 14:
                            found = d
                            return
                    if lk in ("referenceid", "referenceids") and isinstance(v, list):
                        for it in v:
                            if isinstance(it, dict):
                                vv = it.get("Value") or it.get("value")
                                d = _only_digits(str(vv)) if vv else None
                                if d and 12 <= len(d) <= 14:
                                    found = d
                                    return
                    walk(v)
            elif isinstance(o, list):
                for it in o:
                    walk(it)

        walk(st)
        return found
    except:
        return None

async def _resolve_barcode_from_detail(ctx, product_url: str) -> Optional[str]:
    """
    Abre el PDP y prueba en orden: visible > ld+json > __STATE__ > regex.
    """
    dpage = await ctx.new_page()
    try:
        await dpage.goto(product_url, timeout=45000)
        try:
            await dpage.wait_for_load_state("domcontentloaded", timeout=15000)
        except:
            pass

        # 1) visible
        bc = await _barcode_from_visible(dpage)
        if bc:
            return bc

        # 2) JSON-LD
        bc = await _barcode_from_ld_json(dpage)
        if bc:
            return bc

        # 3) VTEX __STATE__
        bc = await _barcode_from_vtex_state(dpage)
        if bc:
            return bc

        # 4) regex cruda
        try:
            html = await dpage.content()
            m = BARCODE_PAT.search(html)
            if m:
                d = m.group(1)
                if 12 <= len(d) <= 14:
                    return d
        except:
            pass

        return None
    finally:
        await dpage.close()

# ---------------------------
# Helpers de precio/promoción
# ---------------------------
def _clean_price(text: str) -> str:
    t = (text or "").strip().replace("\xa0", " ").replace("\u00a0", " ")
    t = t.replace("$", "").replace("ARS", "").strip()
    t = t.replace(".", "").replace(",", ".")
    t = re.sub(r"[^\d.]", "", t)
    if not t:
        return "ARS 0.00"
    try:
        return f"ARS {float(t):.2f}"
    except ValueError:
        return "ARS 0.00"

def _short_promo(text: str) -> Optional[str]:
    if not text:
        return None

    t = " ".join(text.split()).lower()
    # normalizaciones
    t = (t.replace("2°", "2da")
           .replace("2ª", "2da")
           .replace("2do", "2da")
           .replace("seg.da", "segunda")
           .replace("seg. da", "segunda")
           .replace("2°.", "2da"))

    # NxM
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo:
        return f"{m_combo.group(1)}x{m_combo.group(2)}"

    # porcentaje
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    pct = m_pct.group(1) if m_pct else None

    # "segunda unidad" variantes
    unit_words = r"(unidad(?:es)?|unid\.?|ud\.?|u\.?|und\.?)"
    second_words = r"(2da|segunda)"
    patt_direct = rf"(?:\b{second_words}\b.*\b{unit_words}\b|\b{unit_words}\b.*\b{second_words}\b)"
    patt_llevando2 = r"\bllevando\s*2\b"
    patt_positional = rf"\b(en\s+la\s+{second_words}|en\s+{second_words}|{second_words}\s+al|segunda\s+al|{second_words}\s+unidad)\b"

    has_second_unit = bool(
        re.search(patt_direct, t)
        or re.search(patt_llevando2, t)
        or re.search(patt_positional, t)
        or re.search(r"\b2\s*unidades?\b", t)
    )

    if pct:
        return f"2da al {pct}%" if has_second_unit else f"{pct}% OFF"

    return None

# ---------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------
async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    base = "https://diaonline.supermercadosdia.com.ar"
    url = f"{base}/{query}?_q={query}&map=ft"

    page = await ctx.new_page()
    await page.goto(url, timeout=30000)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except:
        pass

    items: List[Dict] = []

    cards = await page.query_selector_all(
        ".vtex-product-summary-2-x-container, .vtex-product-summary-2-x-element, [data-testid*='product-summary']"
    )
    for card in cards[:limit]:
        # --- Título ---
        title = None
        for sel in [
            ".vtex-product-summary-2-x-productBrand",
            ".vtex-store-components-3-x-productBrand",
            ".vtex-product-summary-2-x-productNameContainer",
            "[data-testid*='product-name']",
        ]:
            el = await card.query_selector(sel)
            if el:
                try:
                    title = (await el.inner_text()).strip()
                    if title:
                        break
                except:
                    pass

        # --- URL ---
        href = None
        for sel in ["a.vtex-product-summary-2-x-clearLink", "a"]:
            a = await card.query_selector(sel)
            if a:
                href = await a.get_attribute("href")
                if href:
                    break
        full_url = f"{base}{href}" if href and href.startswith("/") else (href or None)

        # --- Imagen ---
        img = await card.query_selector("img")
        image_url = await img.get_attribute("src") if img else None

        # --- Precio (intenta varios contenedores) ---
        price_text = None
        for sel in [
            ".diaio-store-5-x-sellingPrice",          # específico DIA según CSS
            ".vtex-product-price-1-x-sellingPriceValue",
            ".vtex-store-components-3-x-sellingPrice",
            "[class*='sellingPriceValue']",
            "[class*='sellingPrice']",
        ]:
            el = await card.query_selector(sel)
            if el:
                try:
                    txt = (await el.inner_text()).strip()
                    if re.search(r"\d", txt):
                        price_text = txt
                        break
                except:
                    pass
        price = _clean_price(price_text or "")

        # --- Promoción ---
        promotion = None
        promo_fragments = []
        for sel in [
            # listado
            ".vtex-product-summary-2-x-discountBadgeText",
            ".vtex-product-highlights-2-x-productHighlightText",
            ".vtex-product-highlights-2-x-productHighlightText--promotions",
            # genéricos
            ".discount, .badge, .promo, .teaser",
            "[class*='highlight']",
            "[data-testid*='promo']",
        ]:
            try:
                els = await card.query_selector_all(sel)
                for el in els:
                    try:
                        txt = (await el.inner_text()).strip()
                        if txt:
                            promo_fragments.append(txt)
                    except:
                        pass
            except:
                pass

        # probar fragmentos
        for raw in promo_fragments:
            short = _short_promo(raw)
            if short:
                promotion = short
                break

        # combinar spans
        if not promotion and promo_fragments:
            combined = " | ".join(promo_fragments)
            promotion = _short_promo(combined)

        # fallback: texto de toda la card
        if not promotion:
            try:
                card_text = (await card.inner_text()).strip()
                promotion = _short_promo(card_text)
            except:
                pass

        # --- Barcode (abrir PDP) ---
        barcode_val = None
        if full_url:
            try:
                barcode_val = await _resolve_barcode_from_detail(ctx, full_url)
            except:
                barcode_val = None

        items.append(
            SearchItem(
                title=title,
                price=price,
                url=full_url,
                image_url=image_url,
                barcode=barcode_val,
                promotion=promotion,
            ).dict()
        )

    await page.close()
    return items
