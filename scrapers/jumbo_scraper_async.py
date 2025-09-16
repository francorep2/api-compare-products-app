# scrapers/jumbo_scraper_async.py
import re
import json
from typing import List, Dict, Optional
from app.models.search_item import SearchItem

BARCODE_PAT = re.compile(r"\b(\d{8,14})\b")
NUM_RE = re.compile(r"\d")
RE_SECOND = re.compile(r"(2da|2do|2ª|2°|segunda)\s+.*\s+(\d{1,3})%", re.IGNORECASE)
RE_ANY_PCT = re.compile(r"(-?\s*\d{1,3}\s*%)")

def _only_digits(s: str) -> Optional[str]:
    d = re.sub(r"\D", "", s or "")
    return d or None

def _to_ars(v: Optional[float]) -> str:
    try:
        return f"ARS {float(v):.2f}" if v is not None else "ARS 0.00"
    except:
        return "ARS 0.00"

def _clean_price_text(text: str) -> Optional[float]:
    if not text:
        return None
    t = (text.replace("\xa0", " ").replace("\u00a0", " ").replace("ARS", "").replace("$", ""))
    t = t.replace(" ", "").replace(".", "").replace(",", ".")
    t = re.sub(r"[^\d.]", "", t)
    try:
        return float(t) if t else None
    except ValueError:
        return None

async def _promo_from_dom(root) -> Optional[str]:
    """
    Versión final que extrae correctamente solo el texto de la promoción,
    evitando que se mezcle con el precio u otros datos.
    """
    # Las definiciones de Regex se mantienen igual
    RE_SECOND = re.compile(r"(2da|2do|2ª|2°|segunda)\s+.*\s+\d{1,3}%", re.IGNORECASE)
    RE_ANY_PCT = re.compile(r"(-?\s*\d{1,3}\s*%)")

    selector = '[title*="%"], [class*="promotion"], [class*="discount"], [class*="flag"], span'
    try:
        elements = await root.query_selector_all(selector)
        for el in elements:
            # Estrategia 1: Revisar el atributo 'title'
            title = await el.get_attribute("title")
            if title and "%" in title:
                match_second = RE_SECOND.search(title)
                if match_second:
                    # EXTRAE solo la parte que coincide con la promoción
                    return match_second.group(0).strip()
                
                match_pct = RE_ANY_PCT.search(title)
                if match_pct:
                    return match_pct.group(1).replace(" ", "")

            # Estrategia 2: Revisar el texto interno
            text = (await el.inner_text() or "").strip()
            if text and "%" in text:
                match_second = RE_SECOND.search(text)
                if match_second:
                    # EXTRAE solo la parte que coincide con la promoción
                    return match_second.group(0).strip()

                match_pct = RE_ANY_PCT.search(text)
                if match_pct:
                    return match_pct.group(1).replace(" ", "")
    except Exception:
        return None
    return None

async def _collect_prices_from_root(root) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        nodes = await root.query_selector_all(
            ".vtex-product-price-1-x-sellingPriceValue, .vtex-product-price-1-x-sellingPrice, "
            "[class*='sellingPriceValue'], [class*='sellingPrice'], [class*='vtex-price-format'], "
            ".vtex-product-price-1-x-currencyContainer, [data-testid*='price'], "
            ".vtex-product-price-1-x-listPriceValue, .vtex-product-price-1-x-listPrice, [class*='listPrice']"
        )
        sells, lists = [], []
        for n in nodes:
            cls = (await n.get_attribute("class") or "").lower()
            title = (await n.get_attribute("title") or "").lower()
            txt = (await n.inner_text() or "").strip()
            if not txt or not NUM_RE.search(txt):
                continue
            val = _clean_price_text(txt)
            if not val or val <= 0:
                continue
            if ("listprice" in cls) or ("list price" in title) or ("antes" in title) or ("anterior" in title) or ("tachado" in cls):
                lists.append(val)
            else:
                sells.append(val)
        if sells:
            out["sell"] = min(sells)
        if lists:
            out["list"] = min(lists)
    except:
        pass
    return out

async def _best_selling_from_root(root) -> Optional[float]:
    prices = await _collect_prices_from_root(root)
    if "sell" in prices:
        return prices["sell"]
    if prices:
        return min(prices.values())
    return None

async def _pdp_price_from_dom(page) -> Optional[float]:
    v = await _best_selling_from_root(page)
    if v:
        return v
    try:
        wrappers = await page.query_selector_all(
            ".vtex-product-price-1-x-sellingPrice, .vtex-product-price-1-x-sellingPriceValue, "
            ".vtex-product-price-1-x-priceContainer, [class*='sellingPrice']"
        )
        for w in wrappers:
            cls = (await w.get_attribute("class") or "").lower()
            if "listprice" in cls:
                continue
            int_el = await w.query_selector("[class*='currencyInteger']")
            frac_el = await w.query_selector("[class*='currencyFraction']")
            dec_el = await w.query_selector("[class*='currencyDecimal']")
            int_txt = (await int_el.inner_text()).strip() if int_el else ""
            frac_txt = (await frac_el.inner_text()).strip() if frac_el else ""
            dec_txt = (await dec_el.inner_text()).strip() if dec_el else ","
            composed = f"{int_txt}{dec_txt}{frac_txt}".strip()
            val = _clean_price_text(composed)
            if val and val > 0:
                return val
    except:
        pass
    return None
async def _price_from_card_dom(card) -> Optional[float]:
    """
    Obtiene el precio correcto, manejando tanto la estructura de precio
    simple como la de precio con descuento.
    """
    try:
        # --- ESTRATEGIA 1: Buscar el precio en la estructura simple (ej: $3.217,5) ---
        price_el = await card.query_selector('[class*="vtex-price-format-gallery"]')
        if price_el:
            price_text = (await price_el.inner_text() or "").strip()
            price = _clean_price_text(price_text)
            if price and price > 0:
                return price

        # --- ESTRATEGIA 2 (Fallback): Buscar en la estructura con selling/list price ---
        # Esto es para los productos con descuento visible (precio tachado).
        selling_price_el = await card.query_selector('div[class*="sellingPrice"]:not([class*="listPrice"])')
        if selling_price_el:
            price_text = await selling_price_el.inner_text()
            price = _clean_price_text(price_text)
            if price and price > 0:
                return price

    except Exception:
        return None
    return None
async def _barcode_from_visible(page) -> Optional[str]:
    """
    Versión corregida que extrae el identificador del producto (SKU)
    del 'span' específico que indicaste, con una validación más flexible.
    """
    try:
        # Usamos el selector exacto que nos proporcionaste, es la fuente más fiable.
        selector = "span.vtex-product-identifier-0-x-product-identifier__value"
        
        element = await page.query_selector(selector)
        if element:
            text_content = (await element.inner_text() or "").strip()
            identifier = _only_digits(text_content)
            
            # --- VALIDACIÓN CORREGIDA ---
            # Aceptamos el identificador si tiene 5 o más dígitos,
            # lo cual es una regla más adecuada para un SKU.
            if identifier and len(identifier) >= 5:
                return identifier
                
    except Exception:
        # Si ocurre cualquier error, no devolvemos nada.
        return None
        
    return None

async def _barcode_from_ld_json(page) -> Optional[str]:
    try:
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for s in scripts:
            raw = (await s.inner_text()).strip()
            objs = []
            try:
                objs.append(json.loads(raw))
            except json.JSONDecodeError:
                for m in re.finditer(r"{.*?}", raw, flags=re.DOTALL):
                    try:
                        objs.append(json.loads(m.group(0)))
                    except:
                        pass
            
            for obj in objs:
                for o in (obj if isinstance(obj, list) else [obj]):
                    if isinstance(o, dict) and (o.get("@type", "").lower() == "product"):
                        # --- LÓGICA MEJORADA ---
                        # 1. Buscar explícitamente por GTIN o EAN, que son los correctos.
                        for k in ("gtin13", "gtin14", "gtin", "ean"):
                            v = o.get(k)
                            d = _only_digits(str(v)) if v else None
                            if d and 12 <= len(d) <= 14:
                                return d
                        # 2. Se evita usar "sku", ya que es una causa común de errores.
                        #    El SKU es un código interno, no el código de barras universal.
                        # -----------------------
    except:
        pass
    return None
async def _barcode_from_vtex_state(page) -> Optional[str]:
    try:
        st_json = await page.evaluate("() => { try { return JSON.stringify(window.__STATE__||null) } catch(e){ return null } }")
        if not st_json:
            return None
        
        st = json.loads(st_json)
        candidates = []

        def walk(o):
            if isinstance(o, dict):
                # Extraer todos los valores que parezcan EAN/GTIN
                for k, v in o.items():
                    lk = str(k).lower()
                    # --- LÓGICA MEJORADA ---
                    # Prioriza claves que son explícitamente 'ean' o 'gtin'
                    if any(x in lk for x in ["ean", "gtin"]):
                        d = _only_digits(str(v))
                        if d and 12 <= len(d) <= 14:
                            # Le da más prioridad si la clave es EXACTA
                            priority = 1 if lk in ("ean", "gtin") else 2
                            candidates.append((priority, d))

                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(o, list):
                for it in o:
                    walk(it)
        
        walk(st)

        if not candidates:
            return None
        
        # Ordena los candidatos por prioridad (los más específicos primero)
        candidates.sort()
        return candidates[0][1]

    except:
        return None
async def _resolve_barcode_from_detail(ctx, product_url: str) -> Optional[str]:
    """
    Versión final que PRIORIZA la búsqueda del SKU visible en la página
    antes de buscar el código de barras EAN en los datos estructurados.
    """
    if not product_url:
        return None

    dpage = await ctx.new_page()
    try:
        await dpage.goto(product_url, timeout=45000, wait_until="domcontentloaded")

        # --- ORDEN DE BÚSQUEDA CORREGIDO ---
        # 1. Primero, busca el identificador visible (SKU) que nos indicaste.
        #    Esta es ahora la máxima prioridad.
        # 2. Si no lo encuentra, como plan B, busca el código de barras EAN/GTIN
        #    en los datos estructurados de la página.
        
        barcode = (
            await _barcode_from_visible(dpage)      # <<< AHORA ESTÁ PRIMERO
            or await _barcode_from_ld_json(dpage)
            or await _barcode_from_vtex_state(dpage)
        )

        if barcode:
            return barcode

        return None

    except Exception:
        return None
    finally:
        await dpage.close()

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []

    base = "https://www.jumbo.com.ar"
    url = f"{base}/{query}?_q={query}&map=ft"
    page = await ctx.new_page()

    try:
        
        await page.goto(url, timeout=55000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)

        # --- ESPERA INTELIGENTE ---
        # Espera a que los elementos dinámicos (promociones) carguen.
        # Si no aparecen en 5 segundos, continúa (para productos sin descuento).
        try:
            promo_selector = '[title*="%"], [class*="discount"]'
            await page.wait_for_selector(promo_selector, timeout=8000)
        except:
            pass
        # -------------------------

        items: List[Dict] = []
        
        # Selector robusto para las tarjetas de producto
        card_selector = (
            ".vtex-product-summary-2-x-container, .vtex-search-result-3-x-galleryItem, "
            "[class*='product-summary'], [class*='galleryItem']"
        )
        cards = await page.query_selector_all(card_selector)

        # Si 'cards' está vacío, podría ser una página de producto único
        if not cards:
            title_el = await page.query_selector("h1, .vtex-store-components-3-x-productName")
            title = (await title_el.inner_text()).strip() if title_el else None

            # Si hay título, asumimos que es una página de producto
            if title:
                img_el = await page.query_selector(".vtex-store-components-3-x-productImage img")
                image_url = await img_el.get_attribute("src") if img_el else None
                
                # Usamos las funciones corregidas aquí también
                selling_price = await _pdp_price_from_dom(page) # Asumiendo que tienes una función para PDP
                promotion = await _promo_from_dom(page)
                barcode_val = await _resolve_barcode_from_detail(ctx, page.url)

                items.append(
                    SearchItem(
                        title=title,
                        price=_to_ars(selling_price),
                        url=page.url,
                        image_url=image_url,
                        barcode=barcode_val,
                        promotion=promotion,
                    ).dict()
                )
            return items

        # Bucle para procesar las tarjetas de producto encontradas
        for card in cards[:limit]:
            href_el = await card.query_selector("a")
            href = await href_el.get_attribute("href") if href_el else None
            full_url = f"{base}{href}" if href and href.startswith("/") else (href or None)

            title_el = await card.query_selector(".vtex-product-summary-2-x-productBrand")
            title = (await title_el.inner_text()).strip() if title_el else None
            
            img_el = await card.query_selector("img")
            image_url = await img_el.get_attribute("src") if img_el else None

            # Llamadas a las funciones optimizadas
            selling = await _price_from_card_dom(card)
            promotion = await _promo_from_dom(card)
            
            barcode_val = None
            if full_url:
                try:
                    barcode_val = await _resolve_barcode_from_detail(ctx, full_url)
                except:
                    barcode_val = None

            items.append(
                SearchItem(
                    title=title,
                    price=_to_ars(selling),
                    url=full_url,
                    image_url=image_url,
                    barcode=barcode_val,
                    promotion=promotion,
                ).dict()
            )
            
        return items

    finally:
        await page.close()