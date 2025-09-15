# scrapers/dia_scraper_async.py
import re
from typing import List, Dict, Optional
from app.models.search_item import SearchItem

PROMO_PAT = re.compile(r"(\d{1,3}\s?%|\d+x\d)", re.IGNORECASE)
BARCODE_PAT = re.compile(r"\b(\d{8,14})\b")  # ean8/12/13/14


def _clean_price(text: str) -> str:
    t = text.strip().replace("\xa0", " ").replace("\u00a0", " ")
    t = t.replace("$", "").strip()
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

    # Normalizaciones rápidas de abreviaturas/femenino/masculino
    t = (t
         .replace("2°", "2da")
         .replace("2ª", "2da")
         .replace("2do", "2da")  # forzamos '2da' como salida estándar
         .replace("seg.da", "segunda")
         .replace("seg. da", "segunda")
         .replace("2°.", "2da")
         )

    # Combos NxM (acepta x o ×)
    m_combo = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m_combo:
        return f"{m_combo.group(1)}x{m_combo.group(2)}"

    # Porcentaje
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    pct = m_pct.group(1) if m_pct else None

    # Variantes de "segunda unidad"
    unit_words = r"(unidad(?:es)?|unid\.?|ud\.?|u\.?|und\.?)"
    second_words = r"(2da|segunda)"
    # Casos típicos: "2da unidad", "segunda unidad", "en la 2da", "2da al", "segunda al"
    patt_direct = rf"(?:\b{second_words}\b.*\b{unit_words}\b|\b{unit_words}\b.*\b{second_words}\b)"
    # Frases tipo "llevando 2"
    patt_llevando2 = r"\bllevando\s*2\b"
    # Frases tipo "en la 2da", "en 2da", "2da al", "segunda al"
    patt_positional = rf"\b(en\s+la\s+{second_words}|en\s+{second_words}|{second_words}\s+al|segunda\s+al|{second_words}\s+unidad)\b"

    has_second_unit = bool(
        re.search(patt_direct, t)
        or re.search(patt_llevando2, t)
        or re.search(patt_positional, t)
        or re.search(r"\b2\s*unidades?\b", t)  # “2 unidades” + % ≈ 2da al %
    )

    if pct:
        if has_second_unit:
            return f"2da al {pct}%"
        else:
            return f"{pct}% OFF"

    return None



async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    base = "https://diaonline.supermercadosdia.com.ar"
    url = f"{base}/{query}?_q={query}&map=ft"

    page = await ctx.new_page()
    await page.goto(url, timeout=30000)

    items: List[Dict] = []

    cards = await page.query_selector_all(".vtex-product-summary-2-x-container, .vtex-product-summary-2-x-element")
    for card in cards[:limit]:
        # --- Título ---
        title = None
        for sel in [
            ".vtex-product-summary-2-x-productBrand",
            ".vtex-store-components-3-x-productBrand",
            ".vtex-product-summary-2-x-productNameContainer",
        ]:
            el = await card.query_selector(sel)
            if el:
                title = (await el.inner_text()).strip()
                if title:
                    break

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

        # --- Precio ---
        price_text = None
        for sel in [
                ".vtex-product-price-1-x-sellingPriceValue",
                ".vtex-store-components-3-x-sellingPrice",
                ".diaio-store-5-x-sellingPriceValue",   # <— con punto adelante
                "[class*='sellingPriceValue']"          # fallback genérico
            ]:
            el = await card.query_selector(sel)
            if el:
                price_text = (await el.inner_text()).strip()
                if price_text:
                    break
        price = _clean_price(price_text or "")

        # --- Promoción ---
        promotion = None
        promo_fragments = []

        for sel in [
            # Listado
            ".vtex-product-summary-2-x-discountBadgeText",
            ".vtex-product-highlights-2-x-productHighlightText",
            ".vtex-product-highlights-2-x-productHighlightText--promotions",
            # PDP / ahorros
            ".vtex-product-price-1-x-savings--pdp",
            ".vtex-product-price-1-x-savingsPercentage--pdp",
            # Genéricos
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

        # 1) Probar cada fragmento
        for raw in promo_fragments:
            short = _short_promo(raw)
            if short:
                promotion = short
                break

        # 2) Probar combinando (muchas promos vienen en spans separados)
        if not promotion and promo_fragments:
            combined = " | ".join(promo_fragments)
            promotion = _short_promo(combined)

        # 3) Fallback: todo el texto del card
        if not promotion:
            try:
                card_text = (await card.inner_text()).strip()
                promotion = _short_promo(card_text)
            except:
                pass
                

        # --- Barcode ---
        barcode_val = None
        for attr in [
        ".vtex-product-identifier-0-x-product-identifier__value",
        "[class*='product-identifier__value']",
        ".vtex-product-identifier-0-x-product-identifier",]:
                try:
                    val = await card.get_attribute(attr)
                    if val:
                        cand = re.sub(r"\D", "", str(val))
                        if 8 <= len(cand) <= 14:
                            barcode_val = cand
                            break
                except:
                    pass

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
