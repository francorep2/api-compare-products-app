import re
import json
from typing import List, Dict, Optional
from app.models.search_item import SearchItem

BARCODE_PAT = re.compile(r"\b(\d{8,14})\b")
NUM_RE = re.compile(r"\d")

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
    t = t.replace(" ", "")
    t = t.replace(".", "").replace(",", ".")
    t = re.sub(r"[^\d.]", "", t)
    try:
        return float(t) if t else None
    except ValueError:
        return None

async def _promo_from_dom(root) -> Optional[str]:
    try:
        found = await root.evaluate("""
            (el) => {
              const container = el && el.querySelector ? el : document;
              const ATTRS = ['title','aria-label','data-title','data-tooltip','data-promotion','data-teaser-name','alt'];
              const reMinus = /[\\u2212\\u2013-]\\s*\\d{1,3}\\s*%/;
              const reSecond = /(?:\\b(?:2da|2do|2ª|2°|segunda)\\b(?:\\s+(?:unidad(?:es)?|unid\\.?|ud\\.?|u\\.?|und\\.?))?\\s+(?:al|a)\\s*\\d{1,3}\\s*%)/i;
              const reAny = /\\d{1,3}\\s*%/;
              const all = container.querySelectorAll('*');
              for (const n of all) {
                for (const a of ATTRS) {
                  const v = n.getAttribute && n.getAttribute(a);
                  if (v) {
                    const m = v.match(reMinus) || v.match(reSecond) || v.match(reAny);
                    if (m) return m[0];
                  }
                }
                const t = (n.textContent||'').trim();
                if (!t) continue;
                const m = t.match(reMinus) || t.match(reSecond) || t.match(reAny);
                if (m) return m[0];
              }
              return null;
            }
        """)
        if found:
            return found.replace("−", "-").replace("–", "-").replace(" ", "")
    except:
        pass
    return None

async def _collect_prices_from_root(root) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        nodes = await root.query_selector_all(
            ".vtex-product-price-1-x-sellingPriceValue, "
            ".vtex-product-price-1-x-sellingPrice, "
            "[class*='sellingPriceValue'], "
            "[class*='sellingPrice'], "
            "[class*='vtex-price-format'], "
            ".vtex-product-price-1-x-currencyContainer, "
            "[data-testid*='price'], "
            ".vtex-product-price-1-x-listPriceValue, "
            ".vtex-product-price-1-x-listPrice, "
            "[class*='listPrice']"
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
            ".vtex-product-price-1-x-sellingPrice, "
            ".vtex-product-price-1-x-sellingPriceValue, "
            ".vtex-product-price-1-x-priceContainer, "
            "[class*='sellingPrice']"
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
    v = await _best_selling_from_root(card)
    if v:
        return v
    try:
        wrappers = await card.query_selector_all(
            ".vtex-product-price-1-x-sellingPrice, "
            ".vtex-product-price-1-x-sellingPriceValue, "
            ".vtex-product-price-1-x-priceContainer, "
            "[class*='sellingPrice']"
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

async def _barcode_from_visible(page) -> Optional[str]:
    for sel in [
        ".vtex-product-identifier-0-x-product-identifier__value",
        "[class*='product-identifier__value']",
        ".vtex-product-identifier-0-x-product-identifier",
        "[data-testid*='product-identifier'] span",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                d = _only_digits((await el.inner_text()).strip())
                if d and 12 <= len(d) <= 14:
                    return d
        except:
            pass
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
                        for k in ("gtin13", "gtin", "gtin12", "sku"):
                            v = o.get(k)
                            d = _only_digits(str(v)) if v else None
                            if d and 12 <= len(d) <= 14:
                                return d
    except:
        pass
    return None

async def _barcode_from_vtex_state(page) -> Optional[str]:
    try:
        st_json = await page.evaluate("() => { try { return JSON.stringify(window.__STATE__||null) } catch(e){ return null } }")
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
                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(o, list):
                for it in o:
                    walk(it)
        walk(st)
        return found
    except:
        return None

async def _resolve_barcode_from_detail(ctx, product_url: str) -> Optional[str]:
    dpage = await ctx.new_page()
    try:
        await dpage.goto(product_url, timeout=45000)
        try:
            await dpage.wait_for_load_state("domcontentloaded", timeout=15000)
        except:
            pass
        bc = (
            await _barcode_from_visible(dpage)
            or await _barcode_from_ld_json(dpage)
            or await _barcode_from_vtex_state(dpage)
        )
        if bc:
            return bc
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

async def search(ctx, q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    query = (q or barcode or "").strip()
    if not query:
        return []
    base = "https://www.jumbo.com.ar"
    url = f"{base}/{query}?_q={query}&map=ft"
    page = await ctx.new_page()
    await page.goto(url, timeout=45000)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except:
        pass
    items: List[Dict] = []
    cards = await page.query_selector_all(
        ".vtex-product-summary-2-x-container, "
        ".vtex-product-summary-2-x-element, "
        "[data-testid*='product-summary'], "
        "[class*='productSummary']"
    )
    if not cards:
        title = None
        try:
            h1 = await page.query_selector("h1, [data-testid*='product-name'], [class*='productName']")
            title = (await h1.inner_text()).strip() if h1 else None
        except:
            pass
        image_url = None
        try:
            img = await page.query_selector("picture img, img")
            image_url = await img.get_attribute("src") if img else None
        except:
            pass
        selling = await _pdp_price_from_dom(page)
        promotion = await _promo_from_dom(page)
        barcode_val = await _resolve_barcode_from_detail(ctx, page.url)
        items.append(
            SearchItem(
                title=title,
                price=_to_ars(selling),
                url=page.url,
                image_url=image_url,
                barcode=barcode_val,
                promotion=promotion,
            ).dict()
        )
        await page.close()
        return items
    for card in cards[:limit]:
        href = None
        for sel in ["a.vtex-product-summary-2-x-clearLink", "a"]:
            a = await card.query_selector(sel)
            if a:
                href = await a.get_attribute("href")
                if href:
                    break
        full_url = f"{base}{href}" if href and href.startswith("/") else (href or None)
        title = None
        for sel in [
            ".vtex-product-summary-2-x-productBrand",
            ".vtex-store-components-3-x-productBrand",
            ".vtex-product-summary-2-x-productNameContainer",
            "[data-testid*='product-name']",
            "[class*='productBrand']",
        ]:
            el = await card.query_selector(sel)
            if el:
                try:
                    title = (await el.inner_text()).strip()
                    if title:
                        break
                except:
                    pass
        image_url = None
        for img_sel in ["picture img", "img"]:
            img = await card.query_selector(img_sel)
            if img:
                image_url = (
                    await img.get_attribute("data-src")
                    or await img.get_attribute("srcset")
                    or await img.get_attribute("src")
                )
                if image_url:
                    break
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
    await page.close()
    return items