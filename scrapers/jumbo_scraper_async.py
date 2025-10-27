# scrapers/jumbo_scraper_async.py
import re
import json
import time
import asyncio
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Import the browser manager and types
from app.core.browser_async import browser_manager_async
from playwright.async_api import Page, BrowserContext

# Import your data model (adjust path if needed)
from app.models.search_item import SearchItem 

BASE = "https://www.jumbo.com.ar"

# --- TIMEOUTS FOR KOYEB ---
SCRAPER_BUDGET_SEC = 9.0  # Global timeout
DEFAULT_GOTO_TIMEOUT = 15000 # Increased for potentially slow initial loads
API_TIMEOUT = 25000 # Timeout for API calls
DOM_FALLBACK_TIMEOUT = 25000 # Timeout for DOM fallback navigation
PDP_ENRICH_TIMEOUT = 20000 # Timeout for optional PDP enrichment

# ---------------------------
# Utils básicos (Your original functions)
# ---------------------------
BARCODE_RE = re.compile(r"^\d{8,14}$") 
DIGITS_ANY = re.compile(r"\d+")

def _only_digits(s: Optional[str]) -> Optional[str]:
    if not s: return None
    d = re.sub(r"\D", "", s)
    return d or None

def _normalize_title(t: Optional[str]) -> Optional[str]:
    if not t: return None
    t = re.sub(r"\s+", " ", t).strip()
    return t or None

def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u: return None
    try:
        if u.startswith("/"):
            u = f"{BASE}{u}"
        p = urlparse(u)
        scheme = "https"
        netloc = p.netloc.lower()
        path = p.path or ""
        if path.endswith("/p/"):
            path = path[:-1]
        
        # Keep only essential query parameters (example: maybe 'id' or 'sku' if jumbo uses them)
        essential_params = {'map', 'ft', '_q'} # Add others if needed
        q = [
            (k, v)
            for (k, v) in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() in essential_params 
        ]
        return urlunparse((scheme, netloc, path, "", urlencode(q), ""))
    except Exception:
        return u # Return original URL on error

def _clean_price_to_ars(val: Optional[str | float | int]) -> Optional[str]:
    if val is None: return None
    if isinstance(val, (int, float)):
        return f"ARS {float(val):.2f}"
    t = str(val).strip().replace("\xa0"," ").replace("\u00a0"," ")
    t = t.replace("$","").replace("ARS","").strip()
    t = t.replace(".","").replace(",",".") # Assume '.' is thousand sep, ',' is decimal
    
    # Handle cases like "1.500.99" -> "1500.99"
    parts = t.split('.')
    if len(parts) > 1:
        t = "".join(parts[:-1]) + "." + parts[-1]

    t = re.sub(r"[^\d.]", "", t)
    if not t: return None
    try:
        return f"ARS {float(t):.2f}"
    except ValueError:
        return None # Return None if conversion fails

def _dedupe_and_trim(items: List[Dict], limit: int) -> List[Dict]:
    seen = set()
    out: List[Dict] = []
    for it in items:
        title = _normalize_title(it.get("title"))
        url = _normalize_url(it.get("url"))
        if not title or not url:
            continue
        bc = _only_digits(it.get("barcode"))
        # Use URL as the primary dedupe key, title as secondary
        key = url 
        if key in seen:
            continue
        seen.add(key)
        
        out.append({
            "title": title,
            "url": url,
            "image_url": it.get("image_url") or None,
            "price": it.get("price") or None, # Price already cleaned
            "barcode": bc,
            "promotion": it.get("promotion") or None,
        })
        if len(out) >= limit:
            break
    return out

# ---------------------------
# Parse JSON VTEX (Your original functions)
# ---------------------------
def _find_barcode_in_product(p: Dict) -> Optional[str]:
    # Try common places first
    try:
        items = p.get("items") or []
        if items:
            item = items[0]
            # 1. EAN field
            ean = _only_digits(item.get("ean") or item.get("EAN"))
            if ean and (len(ean) == 8 or 12 <= len(ean) <= 14):
                return ean
            # 2. Reference ID
            refids = item.get("referenceId") or item.get("referenceIds") or []
            if isinstance(refids, list): # Check if it's a list
                 for r in refids:
                     if isinstance(r, dict): # Check if item in list is a dict
                         v = r.get("Value") or r.get("value")
                         d = _only_digits(v)
                         if d and (len(d) == 8 or 12 <= len(d) <= 14):
                            return d
            elif isinstance(refids, dict): # Handle case where it might be a single dict
                 v = refids.get("Value") or refids.get("value")
                 d = _only_digits(v)
                 if d and (len(d) == 8 or 12 <= len(d) <= 14):
                     return d

    except Exception as e:
         print(f"[WARN] Error finding barcode directly: {e}")

    # Fallback: Brute-force search in JSON string (less reliable)
    try:
        txt = json.dumps(p, ensure_ascii=False)
        # Prioritize longer matches first
        for length in (14, 13, 12, 8):
            for d in re.findall(r'\b(\d{' + str(length) + r'})\b', txt):
                return d # Return the first valid EAN-like number found
    except Exception as e:
        print(f"[WARN] Error in brute-force barcode search: {e}")
        
    return None


def _product_to_item(p: Dict) -> Dict:
    title = p.get("productName") or p.get("productTitle") or p.get("productNameWithBrand")
    slug  = p.get("linkText")
    url   = p.get("link") or (f"{BASE}/{slug}/p" if slug else None)

    image_url = None
    price = None
    promotion = None
    
    try:
        items = p.get("items") or []
        if items:
            item = items[0]
            # Image
            imgs = item.get("images") or []
            if imgs: image_url = imgs[0].get("imageUrl")
            
            # Price and Promotion
            sellers = item.get("sellers") or []
            if sellers:
                co = sellers[0].get("commertialOffer") or {}
                if "Price" in co:
                    price = _clean_price_to_ars(co["Price"])
                
                # Promotion (Teasers)
                teasers = co.get("Teasers") or []
                for t in teasers:
                    nm = t.get("name")
                    if nm:
                        promotion = nm # Take the first teaser name
                        break 
    except Exception as e:
        print(f"[WARN] Error parsing product item details: {e}")

    barcode = _find_barcode_in_product(p)

    return {
        "title": title,
        "url": url,
        "image_url": image_url,
        "price": price,
        "barcode": barcode,
        "promotion": promotion,
    }

# ---------------------------
# API VTEX helpers (Your original functions, adjusted timeout)
# ---------------------------
async def _api_get(ctx: BrowserContext, url: str, params: Optional[Dict] = None) -> Optional[List[Dict] | Dict]:
    """GET using ctx.request if available, else fallback to page.evaluate(fetch)."""
    full_url = url
    if params:
        full_url += '?' + urlencode(params)
    print(f"[API GET] {full_url}")

    # Prioritize APIRequestContext if available (more reliable)
    if hasattr(ctx, "request") and ctx.request:
        try:
            resp = await ctx.request.get(url, params=params or {}, timeout=API_TIMEOUT)
            if not resp.ok:
                print(f"[API GET Failed] Status {resp.status} for {full_url}")
                return None
            return await resp.json()
        except Exception as e:
            print(f"[API GET Error] Request failed for {full_url}: {e}")
            return None # Proceed to fallback

    # Fallback using page.evaluate(fetch) - might hit CORS or other issues
    page = await ctx.new_page()
    try:
        # Go to base page first to establish context/cookies if needed
        await page.goto(BASE, timeout=DEFAULT_GOTO_TIMEOUT, wait_until="domcontentloaded") 
        print(f"[API Fallback] Using page.evaluate(fetch) for {full_url}")
        res = await page.evaluate(
            """async ({ url, params }) => {
                const fetchUrl = new URL(url);
                if (params) Object.entries(params).forEach(([k,v]) => fetchUrl.searchParams.set(k, String(v)));
                try {
                    const response = await fetch(fetchUrl.toString(), { 
                        method: 'GET',
                        credentials: 'omit', // Important for avoiding CORS issues
                        headers: { 'Accept': 'application/json' } 
                    });
                    if (!response.ok) {
                         console.error(`Fetch fallback failed with status ${response.status}`);
                         return null;
                    }
                    return await response.json();
                } catch (e) {
                    console.error('Fetch fallback error:', e);
                    return null;
                }
            }""",
            {"url": url, "params": params or {}}
        )
        return res
    except Exception as e:
        print(f"[API Fallback Error] page.evaluate failed for {full_url}: {e}")
        return None
    finally:
        await page.close()


async def _api_search_by_ean(ctx: BrowserContext, ean: str, limit: int) -> List[Dict]:
    data = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"fq": f"alternateIds_Ean:{ean}", "_from": "0", "_to": str(max(limit*2, 1))} # Fetch slightly more just in case
    )
    if not isinstance(data, list):
        print(f"[API EAN] No results or invalid format for EAN {ean}")
        return []
        
    items = [_product_to_item(p) for p in data if isinstance(p, dict)] # Ensure p is a dict
    return _dedupe_and_trim(items, limit)


async def _api_search_by_text(ctx: BrowserContext, q: str, limit: int) -> List[Dict]:
    items: List[Dict] = []
    fetch_count = str(max(limit * 3, 10)) # Fetch more initially

    # 1) Try main search API with 'ft' parameter
    print(f"[API Text] Trying ft={q}")
    data_ft = await _api_get(
        ctx,
        f"{BASE}/api/catalog_system/pub/products/search",
        {"ft": q, "_from": "0", "_to": fetch_count}
    )
    if isinstance(data_ft, list) and data_ft:
        print(f"[API Text] Found {len(data_ft)} results via ft={q}")
        items.extend(_product_to_item(p) for p in data_ft if isinstance(p, dict))

    # 2) Fallback: Try search API with path parameter if 'ft' yielded nothing useful
    if len(items) < limit: # Only try fallback if initial search didn't yield enough
        print(f"[API Text] Trying path /{q}")
        data_path = await _api_get(
            ctx,
            f"{BASE}/api/catalog_system/pub/products/search/{q}",
             {"_from": "0", "_to": fetch_count}
        )
        if isinstance(data_path, list) and data_path:
            print(f"[API Text] Found {len(data_path)} results via path /{q}")
            items.extend(_product_to_item(p) for p in data_path if isinstance(p, dict))

    if not items:
         print(f"[API Text] No results found for query '{q}' via API.")
         
    return _dedupe_and_trim(items, limit)

# ---------------------------
# Fallback DOM light + __STATE__ (Your original functions, adjusted timeout)
# ---------------------------
async def _page_setup_light(page: Page):
    try:
        await page.route("**/*", lambda route: (
            route.abort() if route.request.resource_type in {"image","font","stylesheet","media", "other"}
            else route.continue_()
        ))
    except Exception as e:
        print(f"[WARN] Failed to set up light page routes: {e}")


async def _extract_from_state(page: Page) -> List[Dict]:
    items: List[Dict] = []
    try:
        state_data = await page.evaluate("() => window.__STATE__ || null")
        if not isinstance(state_data, dict):
             print("[DOM Fallback] window.__STATE__ not found or not an object.")
             return []

        print("[DOM Fallback] Processing __STATE__ data...")
        # Recursively search for product-like objects in __STATE__
        
        def find_products(data):
            found = []
            if isinstance(data, dict):
                # Check if this dict looks like a product summary
                typename = data.get("__typename", "")
                is_product = "Product" in typename or "productName" in data
                has_items = "items" in data and isinstance(data["items"], list) and data["items"]
                
                if is_product and has_items:
                    # Attempt to parse it fully
                    parsed = _product_to_item(data)
                    if parsed.get("title") and parsed.get("url"): # Basic validity check
                         found.append(parsed)
                else:
                    # Recursively check values
                    for value in data.values():
                        found.extend(find_products(value))
            elif isinstance(data, list):
                # Recursively check list items
                for item in data:
                    found.extend(find_products(item))
            return found

        items = find_products(state_data)
        print(f"[DOM Fallback] Found {len(items)} potential products in __STATE__.")

    except Exception as e:
        print(f"[ERROR] Failed to extract from __STATE__: {e}")
    return items


async def _extract_from_dom_once(page: Page) -> List[Dict]:
     print("[DOM Fallback] Extracting from rendered DOM...")
     nodes_data = []
     try:
         # More robust selectors for product cards
         product_card_selector = (
             ".vtex-product-summary-2-x-container,"
             ".vtex-search-result-3-x-galleryItem," 
             "[class*='product-summary'],"
             "[class*='galleryItem']" 
         )
         
         # Wait briefly for cards to potentially render
         await page.wait_for_selector(product_card_selector, state="visible", timeout=3000)

         nodes_data = await page.eval_on_selector_all(
             product_card_selector,
             '''(cards) => cards.slice(0, 15).map(n => { // Limit to first 15 cards found
                 const q = s => n.querySelector(s);
                 const txt = el => (el?.textContent || '').trim();

                 let href = '', title = '', image_url = '', price = '';

                 // Link (more specific selectors first)
                 let a = q('a.vtex-product-summary-2-x-clearLink') || q('a[class*="clearLink"]') || q('a[href*="/p"]');
                 if (a) href = a.getAttribute('href') || '';
                 
                 // Title (more specific selectors first)
                 let t = q('span[class*="productBrand"]') || q('h3[class*="productName"]') || q('[data-testid*="product-name"]') || q('h3');
                 if(t) title = txt(t);

                 // Image
                 let img = q('img[class*="image"]');
                 if(img) image_url = img.getAttribute('src') || img.getAttribute('data-src') || '';

                 // Price (look for standard VTEX price structure)
                 let i = q('span[class*="currencyInteger"]')?.textContent || '';
                 let f = q('span[class*="currencyFraction"]')?.textContent || '00';
                 let s = q('span[class*="currencySellingPrice"]') # Check parent for actual price
                 if (s && (i || f)) {
                      # Attempt to reconstruct price text more reliably
                      let priceContainer = s.closest('[class*="priceContainer"]') || s;
                      price = (priceContainer.textContent || '').replace(/\s+/g, ' ').trim();
                 } else {
                     # Fallback to grabbing any price-like element text
                     let pEl = q('[class*="Price"], [class*="price"]')
                     if(pEl) price = txt(pEl);
                 }


                 return { title, url: href, image_url, price };
             })'''
         )
     except Exception as e:
          print(f"[ERROR] Failed extracting from DOM: {e}")
          
     out: List[Dict] = []
     for it in nodes_data or []:
         # Basic check for validity
         if it.get("title") and it.get("url"):
             out.append({
                 "title": _normalize_title(it.get("title")),
                 "url": _normalize_url(it.get("url")),
                 "image_url": absolutize(BASE, it.get("image_url")), # Make image URL absolute
                 "price": _clean_price_to_ars(it.get("price")),
                 "barcode": None, # Cannot get barcode reliably from DOM list
                 "promotion": None, # Cannot get promotion reliably from DOM list
             })
     print(f"[DOM Fallback] Extracted {len(out)} valid items from DOM.")
     return out


async def _dom_fallback(ctx: BrowserContext, q: str, limit: int) -> List[Dict]:
    page = await ctx.new_page()
    print(f"[DOM Fallback] Initiating for query '{q}'")
    try:
        await _page_setup_light(page)
        # Construct search URL (common VTEX pattern)
        url = f"{BASE}/{q}?_q={q}&map=ft" 
        await page.goto(url, timeout=DOM_FALLBACK_TIMEOUT, wait_until="domcontentloaded")
        
        # Give a very brief moment for JS state to potentially populate
        await asyncio.sleep(0.5) 

        # Try __STATE__ first (often contains more data)
        items = await _extract_from_state(page)
        
        # If __STATE__ yielded few/no results, try parsing the rendered DOM
        if len(items) < limit:
            print("[DOM Fallback] __STATE__ insufficient, trying rendered DOM...")
            dom_items = await _extract_from_dom_once(page)
            # Merge results, giving preference to __STATE__ if URLs match
            state_urls = {item['url'] for item in items if item.get('url')}
            for dom_item in dom_items:
                 if dom_item.get('url') and dom_item['url'] not in state_urls:
                      items.append(dom_item)
        
        return _dedupe_and_trim(items, limit)
    except Exception as e:
        print(f"[ERROR] DOM fallback failed critically: {e}")
        return []
    finally:
        await page.close()


# ---------------------------
# (Optional) Enrich PDP - Kept from your code, use with caution on free tier
# ---------------------------
_PDP_CACHE: Dict[str, Tuple[float, Dict]] = {}
_PDP_TTL_SEC = 30 * 60 # 30 minutes cache
_PDP_SEM = asyncio.Semaphore(1) # CONCURRENCY LIMIT = 1 on free tier is safest

async def _pdp_enrich(ctx: BrowserContext, url: str, need_price: bool, need_barcode: bool) -> Dict:
    if not url or (not need_price and not need_barcode): 
        return {"price": None, "barcode": None}
        
    now = time.time()
    cached = _PDP_CACHE.get(url)
    if cached and (now - cached[0] <= _PDP_TTL_SEC):
        data = cached[1]
        print(f"[PDP Enrich] Cache hit for {url}")
        return {
            "price": data.get("price") if need_price else None,
            "barcode": data.get("barcode") if need_barcode else None,
        }

    print(f"[PDP Enrich] Cache miss or expired for {url}. Fetching...")
    async with _PDP_SEM: # Limit concurrency strictly
        page = await ctx.new_page()
        price = None
        barcode = None
        try:
            await _page_setup_light(page)
            await page.goto(url, timeout=PDP_ENRICH_TIMEOUT, wait_until="domcontentloaded")
            await asyncio.sleep(0.5) # Brief wait for potential JS rendering

            if need_price:
                price = await parse_meta_price_dom(page) # Try meta first
                if not price: # Fallback to JS eval
                     price_text = await page.evaluate("""
                         () => {
                             const q = s => document.querySelector(s);
                             const i = q('span[class*="currencyInteger"]')?.textContent || '';
                             const f = q('span[class*="currencyFraction"]')?.textContent || '00';
                             const s = q('span[class*="currencySellingPrice"]');
                             if (s && (i || f)) {
                                 let priceContainer = s.closest('[class*="priceContainer"]') || s;
                                 return (priceContainer.textContent || '').replace(/\s+/g, ' ').trim();
                             }
                             return null;
                         }
                     """, timeout=2000) # Small timeout for JS eval
                     price = _clean_price_to_ars(price_text)
                
            if need_barcode:
                # Try JSON-LD first (often contains EAN)
                jl_data = await parse_jsonld_product(page) 
                if jl_data and jl_data.get("barcode"):
                     barcode = jl_data["barcode"]
                 
                # Fallback: Search __STATE__ if available
                if not barcode:
                    state_items = await _extract_from_state(page) # Reuse state extractor
                    if state_items: # Assume first item is the main product
                         barcode = state_items[0].get("barcode")

            # Update cache BEFORE returning
            result_data = {"price": price, "barcode": barcode}
            _PDP_CACHE[url] = (now, result_data)
            print(f"[PDP Enrich] Fetched and cached for {url}: {result_data}")
            return {
                "price": price if need_price else None, 
                "barcode": barcode if need_barcode else None
            }
        except Exception as e:
             print(f"[ERROR] PDP enrich failed for {url}: {e}")
             # Cache failure result for a short time to avoid retrying immediately
             _PDP_CACHE[url] = (now, {"price": None, "barcode": None}) 
             return {"price": None, "barcode": None}
        finally:
            await page.close()

# ===================================================
# API PÚBLICA PRINCIPAL (CORREGIDA Y CON TIMEOUT GLOBAL)
# ===================================================
async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    """
    Main public API function for the scraper.
    Manages browser context and applies a global timeout for Koyeb compatibility.
    Prioritizes API calls, then falls back to DOM scraping.
    """
    query_input = barcode if barcode else q
    if not query_input or not query_input.strip():
        print("[JUMBO LOG] Search query is empty. Returning [].")
        return []
        
    query = query_input.strip()
    is_barcode_search = bool(barcode and BARCODE_RE.fullmatch(query))

    print(f"[JUMBO LOG] Initiating search with budget {SCRAPER_BUDGET_SEC}s for: '{query}' (IsBarcode: {is_barcode_search})")

    try:
        # --- GLOBAL TIMEOUT WRAPPER ---
        async with asyncio.timeout(SCRAPER_BUDGET_SEC):
            
            # --- GET BROWSER CONTEXT ---
            async with browser_manager_async.context() as ctx:
                
                items: List[Dict] = []
                
                # --- STRATEGY ---
                # 1. If EAN, try EAN API first
                if is_barcode_search:
                    print("[JUMBO LOG] Trying EAN API...")
                    items = await _api_search_by_ean(ctx, query, limit)
                    # If EAN API works, we usually trust it and return
                    if items: 
                         print(f"[JUMBO LOG] EAN API successful. Found {len(items)} items.")
                         return [SearchItem(**item).model_dump() for item in _dedupe_and_trim(items, limit)]

                # 2. Try Text Search API (ft=, then path /)
                print("[JUMBO LOG] Trying Text API...")
                items = await _api_search_by_text(ctx, query, limit)
                
                # 3. If API yielded insufficient results, try DOM Fallback
                if len(items) < limit:
                    print(f"[JUMBO LOG] Text API insufficient ({len(items)}/{limit}). Trying DOM Fallback...")
                    dom_items = await _dom_fallback(ctx, query, limit)
                    # Merge results, prioritize API results for matching URLs
                    api_urls = {item['url'] for item in items if item.get('url')}
                    for dom_item in dom_items:
                         if dom_item.get('url') and dom_item['url'] not in api_urls:
                              items.append(dom_item)
                    items = _dedupe_and_trim(items, limit) # Dedupe again after merging

                if not items:
                     print(f"[JUMBO LOG] No results found for '{query}' after all methods.")
                     return []

                # --- (OPTIONAL) PDP Enrichment ---
                # This part is risky on the free tier due to time/resource limits.
                # Consider enabling only if absolutely necessary and accepting potential timeouts.
                # If you enable it, ensure _PDP_SEM is 1.
                
                # enrich_tasks = []
                # enrich_indices = []
                # for i, item in enumerate(items):
                #     needs_p = item.get("price") is None
                #     needs_b = item.get("barcode") is None and not is_barcode_search # Don't re-fetch barcode if we searched by it
                #     if (needs_p or needs_b) and item.get("url"):
                #          enrich_tasks.append(_pdp_enrich(ctx, item["url"], need_price=needs_p, need_barcode=needs_b))
                #          enrich_indices.append(i)
                
                # if enrich_tasks:
                #     print(f"[JUMBO LOG] Enriching {len(enrich_tasks)} items from PDP...")
                #     enrich_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)
                #     for idx, res in zip(enrich_indices, enrich_results):
                #         if isinstance(res, dict):
                #              if res.get("price"): items[idx]["price"] = res["price"]
                #              if res.get("barcode"): items[idx]["barcode"] = res["barcode"]
                #         elif isinstance(res, Exception):
                #              print(f"[WARN] PDP Enrich failed for item {idx}: {res}")
                # --- End of Optional Enrichment ---

                # Final validation and conversion to SearchItem model
                final_items = _dedupe_and_trim(items, limit)
                print(f"[JUMBO LOG] Search successful. Returning {len(final_items)} items.")
                return [SearchItem(**item).model_dump() for item in final_items]

    except asyncio.TimeoutError:
        print(f"[JUMBO LOG] TIMEOUT GLOBAL ({SCRAPER_BUDGET_SEC}s) reached for: '{query}'. Returning empty list.")
        return []
        
    except Exception as e:
        print(f"[JUMBO ERROR] Critical failure in search function for '{query}': {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
        return []