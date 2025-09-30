import asyncio
import logging
import re
import unicodedata
import inspect
from app.core import browser
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
    if not values:
        return []
    out: List[str] = []
    for v in values:
        if not v:
            continue
        v = re.sub(r"\[\]$", "", v)
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
        out.extend(parts)
    seen = set()
    norm: List[str] = []
    for x in out:
        if x in ALLOWED and x not in seen:
            seen.add(x); norm.append(x)
    return norm

# ------------ Heurísticas de texto ------------
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
def _dedup(seq: list[str]) -> list[str]:
    seen=set(); out=[]
    for s in seq:
        s=" ".join(s.split())
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out

ctx = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
    )
# ------------ ENDPOINT ------------
@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="repetir clave o CSV"),
):
    enabled_set = set(_split_csv(enabled) or ALLOWED)

    # --- FASE 1: BÚSQUEDA DIRECTA Y RESOLUCIÓN DE NOMBRES EN PARALELO ---
    initial_tasks = {
        "coto_direct": search_coto(ctx, barcode=barcode, limit=limit) if "coto" in enabled_set else asyncio.sleep(0, result=[]),
        "name_carrefour": resolve_name_carrefour(barcode) if "carrefour" in enabled_set else asyncio.sleep(0, result=None),
        "name_dia": resolve_name_dia(barcode) if "dia" in enabled_set else asyncio.sleep(0, result=None),
        "name_jumbo": resolve_name_jumbo(barcode) if "jumbo" in enabled_set else asyncio.sleep(0, result=None),
    }
    initial_results = await asyncio.gather(*initial_tasks.values(), return_exceptions=True)
    initial_results_map = dict(zip(initial_tasks.keys(), initial_results))

    def _safe(r):
        return [] if isinstance(r, Exception) else (r or [])

    coto_items = _safe(initial_results_map.get("coto_direct"))
    name_carrefour = initial_results_map.get("name_carrefour") if not isinstance(initial_results_map.get("name_carrefour"), Exception) else None
    name_dia = initial_results_map.get("name_dia") if not isinstance(initial_results_map.get("name_dia"), Exception) else None
    name_jumbo = initial_results_map.get("name_jumbo") if not isinstance(initial_results_map.get("name_jumbo"), Exception) else None

    if not coto_items and "coto" in enabled_set:
        best_name = name_carrefour or name_dia or name_jumbo
        if best_name:
            # Aquí va tu lógica de fallback para Coto si es necesaria
            pass

    # --- FASE 2: BÚSQUEDA POR NOMBRES RESUELTOS EN PARALELO ---
    text_search_tasks = {
        "carrefour": search_carrefour(q=name_carrefour, limit=limit) if name_carrefour and "carrefour" in enabled_set else asyncio.sleep(0, result=[]),
        "dia": search_dia(q=name_dia, limit=limit) if name_dia and "dia" in enabled_set else asyncio.sleep(0, result=[]),
        "jumbo": search_jumbo(q=name_jumbo, limit=limit) if name_jumbo and "jumbo" in enabled_set else asyncio.sleep(0, result=[]),
    }
    text_search_results = await asyncio.gather(*text_search_tasks.values(), return_exceptions=True)
    text_search_results_map = dict(zip(text_search_tasks.keys(), text_search_results))

    # --- FASE 3: UNIFICAR RESULTADOS ---
    buckets: Dict[str, List[SearchItem]] = {k: [] for k in ALLOWED}
    if "coto" in enabled_set: buckets["coto"] = coto_items
    if "carrefour" in enabled_set: buckets["carrefour"] = _safe(text_search_results_map.get("carrefour"))
    if "dia" in enabled_set: buckets["dia"] = _safe(text_search_results_map.get("dia"))
    if "jumbo" in enabled_set: buckets["jumbo"] = _safe(text_search_results_map.get("jumbo"))
    
    results_enabled = {k: buckets.get(k, []) for k in enabled_set}
    total = sum(len(v or []) for v in results_enabled.values())

    return {
        "results": results_enabled,
        "count": total,
        "enabled": list(enabled_set),
        "barcode": barcode,
        "limit": limit,
    }