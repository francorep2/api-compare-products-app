import re
import unicodedata
from typing import Set, List, Dict, Optional

# --- CONSTANTES Y DICCIONARIOS DE HEURÍSTICA ---
STOP = {"de","del","la","el","los","las","con","sin","x","por","y","en","a","al","para","un","una"}
CAT_FAMILY = {"leche", "galletitas", "pan", "queso", "fideos", "arroz", "jabon", "shampoo"} # (versión simplificada)
VARIANTS = {"light", "zero", "integral", "chocolate", "vainilla"} # (versión simplificada)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = _strip_accents(s)
    # Patrón mejorado para no separar números de unidades comunes (ej: 1l, 500g)
    s = re.sub(r'(\d+)\s*(lts?|kgs?|grs?|ml|g|kg)\b', r'\1\2', s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def _tokens(s: str) -> list[str]:
    toks = _norm(s).split()
    return [t for t in toks if t not in STOP]

def _is_size_or_unit(token: str) -> bool:
    """Verifica si un token es una unidad de medida o un tamaño."""
    return re.fullmatch(r'\d+(lts?|kgs?|grs?|ml|g|kg|unidades|u)', token) is not None

# --- FUNCIONES DE COMPARACIÓN Y SELECCIÓN (Mejoradas) ---

def _similarity(a: str, b: str) -> float:
    # (Tu función de similitud es buena, la mantenemos)
    A, B = set(_tokens(a)), set(_tokens(b))
    return (len(A & B) / len(A | B)) if A and B else 0.0

def _split_keywords(q: str) -> (Set[str], Set[str], Set[str]):
    toks, brands, cats, desc = _tokens(q), [], [], []
    for t in toks:
        if _is_size_or_unit(t): # <-- IGNORAMOS LOS TAMAÑOS
            continue
        if t in CAT_FAMILY: cats.append(t)
        elif t in VARIANTS: desc.append(t)
        else: brands.append(t)
    # ... (tu lógica de dedup)
    return set(brands), set(cats), set(desc)

def _must_match(title: str, brand_tokens: Set[str], cat_tokens: Set[str]) -> bool:
    T = set(_tokens(title))
    # Ahora es más flexible: debe coincidir la marca, y si especificamos categoría, también.
    has_brand = (len(T & brand_tokens) > 0) if brand_tokens else True
    has_cat = (len(T & cat_tokens) > 0) if cat_tokens else True
    return has_brand and has_cat
    
def pick_best_one(
    candidates: List[Dict], 
    expected_name: str, 
    brand_tokens: Set[str], 
    cat_tokens: Set[str], 
    min_score=0.25 # <-- 1. UMBRAL MÁS BAJO (antes 0.40)
) -> Optional[Dict]:
    best_item, best_score = None, -1.0
    
    # --- 2. LOGS DE DEPURACIÓN ---
    print(f"\n--- Analizando '{expected_name}' ---")
    print(f"[Keywords]: brands={brand_tokens}, cats={cat_tokens}")

    for item in (candidates or []):
        title = (item.get("title") or "").strip()
        if not title: continue
        
        print(f"  [Candidato]: {title}")
        
        passes_must_match = _must_match(title, brand_tokens, cat_tokens)
        if not passes_must_match:
            print("    -> Descartado por: No pasar _must_match")
            continue
            
        score = _similarity(title, expected_name)
        print(f"    -> Score Similitud: {score:.2f}")

        if score > best_score:
            best_item, best_score = item, score
            
    print(f"[Mejor Match]: {best_item.get('title') if best_item else 'Ninguno'}")
    print(f"[Mejor Score]: {best_score:.2f}")
    if best_item and best_score < min_score:
        print(f"  -> Descartado por: Score < umbral ({min_score})")
        return None
        
    return best_item


def find_best_match_for_item(item_name: str, results: List[Dict]) -> Optional[Dict]:
    if not results:
        return None
    brand_tokens, cat_tokens, _ = _split_keywords(item_name)
    return pick_best_one(
        candidates=results,
        expected_name=item_name,
        brand_tokens=brand_tokens,
        cat_tokens=cat_tokens
    )