from typing import List, Dict, Optional, Any
import json, urllib.parse, re, time, datetime, os, asyncio, unicodedata, contextlib

# Importa el manager del otro archivo
from app.core.browser_async import browser_manager_async
from scrapers._utils_promos import PROMO_NORMALIZER

# ===================== Config =====================

TOP_N = 3
COTO_BASE = "https://www.cotodigital.com.ar"
COTO_SUCURSAL_ID = 200

# --- TIMEOUTS BRUTALES (PARA EL PLAN GRATUITO) ---

# 1. Presupuesto TOTAL: 9 segundos.
# Si el scraper (incluyendo el arranque) tarda más, lo cortamos.
SCRAPER_BUDGET_SEC = 9.0 

# 2. Presupuestos Internos (lo que queda *después* del arranque)
DEFAULT_SEARCH_TIMEOUT_MS = 3500  # 3.5s para que CARGUE la búsqueda
DEFAULT_SELECTOR_TIMEOUT_MS = 2500 # 2.5s para que APAREZCAN los productos

# ===================== Utils (Tus funciones) =====================

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return re.sub(r"\s+", " ", s).strip()

def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_barcode(s: Optional[str]) -> Optional[str]:
    d = _digits(s)
    return d if 8 <= len(d) <= 14 else None

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
            # Bloqueamos todo lo que no sea script o documento
            if route.request.resource_type in {"image", "font", "media", "stylesheet", "other"}:
                return await route.abort()
        except Exception:
            pass
        await route.continue_()
    await page.route("**/*", _route_handler)

# ===================== Lógica del Scraper (Modo Rápido) =====================

async def coto_search_fast(ctx, query: str, limit: int = TOP_N) -> List[Dict]:
    """
    Función ÚNICA de scraping.
    Solo entra a la página de búsqueda y extrae los datos de la "card".
    No entra a las páginas de producto (PDP) para evitar timeouts.
    """
    search_url = (
        f"{COTO_BASE}/sitios/cdigi/categoria"
        f"?_dyncharset=utf-8&Dy=1&Ntt={urllib.parse.quote(query)}&idSucursal={COTO_SUCURSAL_ID}"
    )
    
    page = await ctx.new_page()
    await _enable_speed_routes(page) # Bloquear imágenes, css, etc.
    
    try:
        print(f"[LOG] Coto Fast GET {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=DEFAULT_SEARCH_TIMEOUT_MS)

        anchors_selector = "a[href*='/sitios/cdigi/productos/']"
        await page.wait_for_selector(anchors_selector, timeout=DEFAULT_SELECTOR_TIMEOUT_MS)
        
        # Evaluar en el navegador (más rápido que usar page.locator)
        card_data = await page.eval_on_selector_all(
            anchors_selector,
            """(anchors, limit) => {
                const results = [];
                const seen = new Set();
                for (let a of anchors) {
                    if (results.length >= limit) break;
                    
                    const href = a.getAttribute('href');
                    if (!href || seen.has(href)) continue;
                    seen.add(href);

                    const card = a.closest('[class*="product"]');
                    let title = null;
                    if (card) {
                        const titleEl = card.querySelector('[data-testid="product-title"]');
                        if (titleEl) title = titleEl.textContent;
                    }
                    
                    results.push({ href: href, title: title });
                }
                return results;
            }""",
            limit
        )

        # Mapear a tu template
        results: List[Dict] = []
        for item in card_data:
            full_url = absolutize(COTO_BASE, item['href'])
            
            # Intentar sacar EAN/SKU de la URL (si existe)
            barcode = None
            m = re.search(r'-([0-9]{8,14})(?:-|$)', full_url)
            if m:
                barcode = normalize_barcode(m.group(1))

            results.append({
                "title": clean_text(item['title']),
                "price": None, # No podemos obtenerlo sin entrar al PDP
                "url": full_url,
                "image_url": None, # No podemos obtenerlo sin entrar al PDP
                "barcode": barcode,
                "promotion": None # No podemos obtenerlo sin entrar al PDP
            })

        return results[:limit]

    except Exception as e:
        print(f"[LOG] Browser search error: {e}")
        # Si Coto tarda mucho, fallará aquí (Timeout)
        return []
    finally:
        await page.close()

# ===================== API pública del scraper (Con TIMEOUT GLOBAL) =====================

async def search(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = TOP_N) -> List[Dict]:
    """
    Punto de entrada principal.
    Obtiene un contexto y ejecuta el scraper con un timeout global.
    """
    
    query_to_search = q
    if barcode:
        query_to_search = barcode # Coto busca EANs en la barra normal
    
    if not query_to_search:
        return []

    print(f"[LOG] Iniciando búsqueda con budget de {SCRAPER_BUDGET_SEC}s para: '{query_to_search}'")
    
    try:
        # --- EL SAFETY NET (LA LLAVE) ---
        # Ejecuta la tarea, pero si tarda más de 9.0s, la cancela.
        async with asyncio.timeout(SCRAPER_BUDGET_SEC):
            async with browser_manager_async.context() as ctx:
                
                ean = normalize_barcode(barcode or (q or ""))
                
                if ean and (not q or q.strip() == ean):
                    # Búsqueda por Barcode
                    return await coto_search_fast(ctx, ean, limit=limit)
                    
                if q and q.strip():
                    # Búsqueda por Query
                    return await coto_search_fast(ctx, q.strip(), limit=limit)
                    
                return []
                
    except asyncio.TimeoutError:
        # ¡ÉXITO! (para nosotros)
        # El scraper tardó más de 9s (por el cold start) y lo cortamos
        # antes de que Koyeb nos diera 504.
        print(f"[LOG] TIMEOUT GLOBAL ({SCRAPER_BUDGET_SEC}s) alcanzado para: '{query_to_search}'. Devolviendo [].")
        return []
        
    except Exception as e:
        # Cualquier otro error (ej: Playwright no pudo arrancar)
        print(f"[ERROR] Falla crítica en 'search': {e}")
        return []