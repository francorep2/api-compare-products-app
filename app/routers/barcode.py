import asyncio
import logging
import re
import unicodedata
import inspect
from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Dict, List, Optional

from app.models.search_item import SearchItem
from app.services.coto import search_coto
from app.services.peer_name_resolver import resolve_product_name_from_peers
from app.services.jumbo import search_jumbo
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from scrapers.barcode_resolver import (
    resolve_name_carrefour,
    resolve_name_dia,
    resolve_name_jumbo,
)
from app.dependencies.auth import get_current_user, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/search/barcode", tags=["barcode"], dependencies=[Depends(get_current_user)])

# ------------ Normalización de enabled ------------
ALLOWED = {"carrefour", "coto", "jumbo", "dia"}

def _split_csv(values: Optional[List[str]]) -> List[str]:
    """
    Acepta:
      - enabled=coto&enabled=jumbo
      - enabled=coto,jumbo
      - enabled[]=coto&enabled[]=jumbo
    Devuelve una lista normalizada, sin duplicados y orden estable.
    """
    if not values:
        return []
    out: List[str] = []
    for v in values:
        if not v:
            continue
        v = re.sub(r"\[\]$", "", v)  # enabled[] -> enabled
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
        out.extend(parts)
    seen = set()
    norm: List[str] = []
    for x in out:
        if x in ALLOWED and x not in seen:
            seen.add(x); norm.append(x)
    return norm

# ------------ Heurísticas de texto (tu código original) ------------
STOP = {
    "de","del","la","el","los","las","con","sin","x","por","y","en","a","al","para",
    "un","una","lo","su","sus","mi","mis","tu","tus"
}
UNITS = {"g","gr","grs","gramo","gramos","kg","kgs","kilo","kilos","ml","cc","l","lt","lts","litro","litros","unidad","unidades","cm3"}
SIZE_PAT = r"\b(\d{1,5})\s*(g|gr|grs|kg|ml|l|lt|lts|cc|cm3|kilos?|litros?)\b"
PACK_HINTS = {
    "pack","packx","pack2","pack3","pack4","pack6","pack12","pack24","x2","x3","x4","x6","x12","x24",
    "combo","multipack","promo","doble","triple"
}
CAT_FAMILY = {
    "arroz","fideos","pasta","pastas","harina","azucar","sal","cafe","té","te","mate","yerba",
    "salsa","salsas","pure","puré","caldo","caldos","sopa","sopas","aceite","vinagre","cereal","cereales",
    "legumbre","legumbres","lenteja","lentejas","poroto","porotos","garbanzo","garbanzos",
    "galleta","galletitas","cookie","cookies","alfajor","alfajores","snack","snacks","chips","tostadas",
    "bizcocho","bizcochos","barrita","barras","chocolate","chocolates","caramelo","caramelos","gomitas",
    "pan","panes","facturas","budin","budines","bizcochuelo","bizcochuelos",
    "leche","leches","yogur","yogurt","queso","quesos","manteca","crema","dulce","flan","postre","postres",
    "bebida","bebidas","agua","aguas","soda","sodas","gaseosa","gaseosas","jugo","jugos","energizante","energizantes",
    "isotonica","isotónica","cerveza","cervezas","vino","vinos","sidra","fernet","whisky","vodka","ron","licor","aperitivo",
    "jabon","jabón","shampoo","acondicionador","suavizante","detergente","lavandina","desinfectante","toalla","toallas",
    "papel","higienico","higiénico","servilleta","servilletas","pañal","pañales","protector","solar",
    "alimento","alimentos","perro","perros","gato","gatos","balanceado",
    "carne","pollo","cerdo","pescado","fiambre","queso","fruta","verdura","huevo","huevos",
}
FORMS = {
    "polvo","instantaneo","instantanea","instantánea","sobres","sobre","saquitos",
    "botella","lata","latita","tetra","brick","doypack","frasco","bolsa","sachet","caja","bandeja",
    "bidon","bidón","granel"
}
VARIANTS = {
    "vainilla","chocolate","menta","mentol","dulce","amargo","coco","almendra","avellana","avena",
    "frutilla","manzana","pera","uva","anana","ananá","durazno","ciruela","cereza","pomelo","naranja",
    "limon","limón","maracuya","maracuyá","mango","sandia","sandía","melon","melón","tropical","citrus",
    "light","zero","sin","azucar","azúcar","integral","clasico","clásico","tradicional","suave","intenso",
    "extra","premium","selecto","económico","económico",
    "lavanda","limpio","limpieza","antibacterial","antibacteriano","antisarro","limon","limón","floral","primavera",
}
BRANDS_HINT = {
    "arcor","tang","clight","bc","ser","manaos","coca","coca-cola","pepsi","aquarius","villavicencio","levite","levité",
    "glaciar","eco","gatorade","powerade","speed","redbull","red bull","monster","quilmes","andes","brahma","heineken",
    "oreo","tita","rhodesia","melba","cachafaz","pepitos","criollitas","express","crackers","bagley","terrabusi","pehuamar",
    "lays","pringles","milka","cadbury","felfort","bonafide","havanna","cofler","shot","block","mantecol",
    "la serenisima","serenisima","ilolay","sancor","milkaut","veronica","danone","yogurisimo","ilolay",
    "coto","dia","jumbo","carrefour",
}
SYN = {
    "gramos":"g","gramo":"g","grs":"g","gr":"g","kilo":"kg","kilos":"kg","lts":"l","lt":"l","litros":"l","litro":"l",
    "centimetros cubicos":"cc","cm3":"cc",
    "tetrapak":"tetra","doy pack":"doypack","bidón":"bidon","latitas":"lata",
    "limón":"limon","ananá":"anana","sandía":"sandia","melón":"melon","instantánea":"instantanea","azúcar":"azucar",
    "galletita":"galletitas","cookie":"cookies","bebidas":"bebida","yogurt":"yogur","higiénico":"higienico",
    "redbull":"red bull","la serenísima":"la serenisima",
}
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
def _canon(tok: str) -> str:
    return SYN.get(tok, tok)
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = _strip_accents(s)
    s = re.sub(SIZE_PAT, lambda m: f"{m.group(1)}{SYN.get(m.group(2), m.group(2))}", s)
    s = re.sub(r"\bpack\s*x\s*(\d+)\b", r"pack\1", s)
    s = re.sub(r"\bx\s*(\d+)\b", r"x\1", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()
def _tokens(s: str) -> list[str]:
    toks = _norm(s).split()
    out = []
    for t in toks:
        if t in STOP: continue
        out.append(_canon(t))
    return out
def _extract_sizes(tokens: list[str]) -> list[str]:
    sizes = [t for t in tokens if re.match(r"^\d{1,5}(g|kg|ml|l|cc)$", t)]
    seen=set(); out=[]
    for t in sizes:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
def _extract_packs(tokens: list[str]) -> list[str]:
    packs = []
    for t in tokens:
        if t in PACK_HINTS or re.match(r"^(pack\d+|x\d+)$", t):
            packs.append(t)
    seen=set(); out=[]
    for t in packs:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
def _extract_brand(tokens: list[str]) -> str | None:
    for t in tokens:
        if t in BRANDS_HINT:
            return t
    for t in tokens:
        if t in CAT_FAMILY or t in FORMS or t in VARIANTS: continue
        if t in PACK_HINTS or re.match(r"^\d", t): continue
        if re.match(r"^(x\d+|pack\d+)$", t): continue
        return t
    return None
def _extract_categories(tokens: list[str]) -> list[str]:
    cats = [t for t in tokens if t in CAT_FAMILY]
    seen=set(); out=[]
    for t in cats:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
def _extract_forms(tokens: list[str]) -> list[str]:
    forms = [t for t in tokens if t in FORMS]
    seen=set(); out=[]
    for t in forms:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
def _extract_variants(tokens: list[str]) -> list[str]:
    vars_ = [t for t in tokens if t in VARIANTS]
    seen=set(); out=[]
    for t in vars_:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
def _dedup(seq: list[str]) -> list[str]:
    seen=set(); out=[]
    for s in seq:
        s=" ".join(s.split())
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out

def build_generic_queries(q: str, max_queries: int = 10) -> list[str]:
    toks = _tokens(q)
    brand = _extract_brand(toks)
    cats  = _extract_categories(toks)
    forms = _extract_forms(toks)
    vars_ = _extract_variants(toks)
    sizes = _extract_sizes(toks)
    packs = _extract_packs(toks)
    base_cat = cats[0] if cats else "producto"
    form_str = " ".join(forms[:2]) if forms else ""
    var_str  = " ".join(vars_[:2]) if vars_ else ""
    size_1   = sizes[0] if sizes else ""
    pack_1   = packs[0] if packs else ""
    combos: list[str] = []
    if brand and var_str:
        combos += [
            f"{base_cat} {form_str} {brand} {var_str}",
            f"{base_cat} {brand} {var_str}",
            f"{brand} {var_str}",
        ]
    if brand:
        combos += [f"{base_cat} {form_str} {brand}", f"{base_cat} {brand}", f"{brand}"]
    if var_str:
        combos += [f"{base_cat} {form_str} {var_str}", f"{base_cat} {var_str}"]
    if form_str:
        combos += [f"{base_cat} {form_str}"]
    combos += [f"{base_cat}"]
    if size_1:
        combos += [
            f"{base_cat} {brand or ''} {var_str} {size_1}",
            f"{brand or ''} {var_str} {size_1}",
            f"{base_cat} {size_1}",
        ]
    if pack_1:
        combos += [
            f"{base_cat} {brand or ''} {var_str} {pack_1}",
            f"{base_cat} {pack_1}",
        ]
    if len(cats) >= 2:
        combos.insert(0, " ".join(cats[:2]))
    return _dedup([c.strip() for c in combos if c.strip()])[:max_queries]

def _similarity(a: str, b: str) -> float:
    A, B = set(_tokens(a)), set(_tokens(b))
    return (len(A & B) / len(A | B)) if A and B else 0.0

def _split_keywords(q: str):
    toks = _tokens(q)
    brands, cats, desc = [], [], []
    for t in toks:
        if t in CAT_FAMILY:
            cats.append(t)
        elif t in VARIANTS:
            desc.append(t)
        else:
            brands.append(t)
    def dedup(seq):
        seen = set(); out = []
        for x in seq:
            if x not in seen:
                seen.add(x); out.append(x)
        return out
    cats_norm = ["cookies"] if cats else []
    return set(dedup(brands)), set(cats_norm), set(dedup(desc))

def _must_match(title: str, brand_tokens: set[str], cat_tokens: set[str]) -> bool:
    T = set(_tokens(title))
    has_brand = (len(T & brand_tokens) >= 1) if brand_tokens else True
    has_cat = (len(T & CAT_FAMILY) >= 1) if cat_tokens else True
    return has_brand and has_cat

def pick_best_one(cand: list[dict], expected: str, brand_tokens: set[str], cat_tokens: set[str], min_score=0.50) -> list[dict]:
    best=None; best_sc=-1.0
    for it in (cand or []):
        title = (it.get("title") or "").strip()
        if not _must_match(title, brand_tokens, cat_tokens):
            continue
        sc = _similarity(title, expected)
        if sc > best_sc:
            best, best_sc = it, sc
    return [best] if (best and best_sc >= min_score) else []

async def coto_search_safe(q_text: str, limit: int):
    sig = inspect.signature(search_coto)
    params = sig.parameters
    text_param = None
    for candidate in ("q", "query", "text", "term", "s", "search"):
        if candidate in params:
            text_param = candidate
            break
    if text_param is None:
        for name, p in params.items():
            if name != "self" and name != "limit":
                text_param = name
                break
    kwargs = {}
    if text_param:
        kwargs[text_param] = str(q_text)
    if "limit" in params:
        kwargs["limit"] = max(5, int(limit))
    return await search_coto(**kwargs)

# ------------ ENDPOINT ------------
@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="repetir clave o CSV"),
):
    # Normalizar enabled; por defecto, todos
    enabled_norm = _split_csv(enabled)
    enabled_set = set(enabled_norm or ALLOWED)

    # Resolver nombre “base” por peers para mejorar fallback de Coto
    q: Optional[str] = await resolve_product_name_from_peers(barcode)

    # --- COTO: intento directo por barcode; si no hay, fallback por texto heurístico ---
    coto_items: List[SearchItem] = []
    if "coto" in enabled_set:
        try:
            coto_direct = await search_coto(barcode, limit)
        except Exception:
            coto_direct = []
        coto_items = coto_direct
        if not coto_items and q:
            try:
                brands, cats, _ = _split_keywords(q)
            except Exception:
                brands, cats = set(), set()
            queries = build_generic_queries(q, max_queries=8)
            for q_try in queries:
                try:
                    cand = await coto_search_safe(q_try, max(5, int(limit)))
                except Exception:
                    cand = []
                picked = pick_best_one(
                    cand,
                    expected=q,
                    brand_tokens=brands,
                    cat_tokens=cats,
                    min_score=0.40
                )
                if picked:
                    if not picked[0].get("barcode"):
                        picked[0]["barcode"] = barcode
                    coto_items = picked
                    break

    # --- Resolver nombres por tienda (Carrefour/Día/Jumbo) para mejorar recall ---
    name_carrefour, name_dia, name_jumbo = await asyncio.gather(
        resolve_name_carrefour(barcode) if "carrefour" in enabled_set else asyncio.sleep(0, result=None),
        resolve_name_dia(barcode)        if "dia"        in enabled_set else asyncio.sleep(0, result=None),
        resolve_name_jumbo(barcode)      if "jumbo"      in enabled_set else asyncio.sleep(0, result=None),
    )

    # --- Ejecutar búsquedas por tienda según enabled ---
    tasks = []
    if "carrefour" in enabled_set:
        tasks.append(
            search_carrefour(q=name_carrefour, limit=limit) if name_carrefour
            else asyncio.sleep(0, result=[])
        )
    if "dia" in enabled_set:
        tasks.append(
            search_dia(q=name_dia, limit=limit) if name_dia
            else asyncio.sleep(0, result=[])
        )
    if "jumbo" in enabled_set:
        tasks.append(
            search_jumbo(q=name_jumbo, limit=limit) if name_jumbo
            else asyncio.sleep(0, result=[])
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _safe(r):
        return [] if isinstance(r, Exception) else (r or [])

    # mapear en el mismo orden en que agregamos tasks
    carrefour: List[SearchItem] = []
    dia_res: List[SearchItem] = []
    jumbo: List[SearchItem] = []

    # reconstruir asignando por tienda presente
    i = 0
    if "carrefour" in enabled_set:
        carrefour = _safe(results[i]); i += 1
    if "dia" in enabled_set:
        dia_res = _safe(results[i]); i += 1
    if "jumbo" in enabled_set:
        jumbo = _safe(results[i]); i += 1

    # --- Armar salida estándar ---
    buckets: Dict[str, List[SearchItem]] = {k: [] for k in ALLOWED}
    if "carrefour" in enabled_set: buckets["carrefour"] = carrefour
    if "dia" in enabled_set:        buckets["dia"] = dia_res
    if "jumbo" in enabled_set:      buckets["jumbo"] = jumbo
    if "coto" in enabled_set:       buckets["coto"] = coto_items

    # Sólo devolver las tiendas enabled (pero manteniendo claves vacías si querés)
    results_enabled = {k: buckets.get(k, []) for k in enabled_set}

    total = sum(len(v or []) for v in results_enabled.values())

    return {
        "results": results_enabled,             # dict tienda -> list[SearchItem]
        "count": total,                          # total items
        "enabled": list(enabled_set),            # eco de la selección
        "barcode": barcode,                      # útil para debug/cliente
        "limit": limit,
    }
