import asyncio
from fastapi import APIRouter, Query
from typing import Dict, List, Optional
from app.models.search_item import SearchItem
from app.services.barcode.jumbo_barcode import search_jumbo_barcode
from app.services.barcode.carrefour_barcode import search_carrefour_barcode
from app.services.barcode.dia_barcode import search_dia_barcode 
from app.services.coto import search_coto
from app.services.peer_name_resolver import resolve_product_name_from_peers
import logging, re, unicodedata, inspect

log = logging.getLogger(__name__)
router = APIRouter(prefix="/search/barcode", tags=["barcode"])

STOP = {"de","del","la","el","los","las","con","sin","x","por","y","en","a"}
CATS = {"galleta","galletita","galletitas","cookie","cookies"}
DESC = {"chocolate","chips","multifruta","vainilla","frutilla"}
CAT_FAMILY = {"galleta", "galletita", "galletitas", "cookie", "cookies"}

def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(\d+)\s*(g|gr|grs|gramos|ml|lts?|kg)\b\.?", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t and t not in STOP]

def _similarity(a: str, b: str) -> float:
    A, B = set(_tokens(a)), set(_tokens(b))
    return (len(A & B) / len(A | B)) if A and B else 0.0

def _split_keywords(q: str):
    toks = _tokens(q)
    brands, cats, desc = [], [], []
    for t in toks:
        if t in CAT_FAMILY:
            cats.append(t)
        elif t in DESC:
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

def build_coto_queries(q: str) -> list[str]:
    brands, cats, desc = _split_keywords(q)
    b = next(iter(brands), "")
    c = next(iter(cats), "")
    d = next(iter(desc), "")
    candidates: list[str] = []
    if b and c:
        if d: candidates.append(f"{c} {b} {d}")
        candidates.append(f"{c} {b}")
    if b and d:
        candidates.append(f"{b} {d}")
    if b:
        candidates.append(b)
    seen=set(); out=[]
    for s in candidates:
        s=s.strip()
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out[:3]

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

@router.get("/all", response_model=Dict[str, List[SearchItem]])
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
):
    q: Optional[str] = await resolve_product_name_from_peers(barcode)
    log.info(f"[BARCODE ALL] EAN={barcode} -> q peers: {q!r}")

    tasks = [
        search_jumbo_barcode(barcode, limit),
        search_carrefour_barcode(barcode, limit),
        search_dia_barcode(barcode, limit),
    ]

    coto_items: List[SearchItem] = []
    if q:
        brands, cats, desc = _split_keywords(q)
        queries = build_coto_queries(q)
        log.info(f"[COTO] tries={queries} (from {q!r})")

        for q_try in queries:
            log.info(f"[COTO] trying query={q_try!r}")
            try:
                cand = await coto_search_safe(q_try, max(5, int(limit)))
                log.info(f"[COTO] q_try={q_try!r} -> {len(cand)} resultados")
            except Exception as e:
                log.warning(f"[COTO] search_coto error q={q_try!r}: {e}")
                cand = []

            early = None
            for it in cand or []:
                title = (it.get("title") or "").lower()
                if "cofler" in title and any(x in title for x in ("cookies","galletita","galletitas","cookie")):
                    if "120" in _norm(q):
                        if "120" in title:
                            early = it
                            break
                    else:
                        early = it
                        break
            if early:
                if not early.get("barcode"):
                    early["barcode"] = barcode
                coto_items = [early]
                log.info(f"[COTO] early picked title={early.get('title')!r}")
                break

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
                log.info(f"[COTO] picked title={picked[0].get('title')!r}")
                break

        if not coto_items:
            log.info(f"[COTO] no acceptable match for q={q!r} after tries={queries}")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _safe(i):
        return [] if isinstance(i, Exception) else (i or [])

    jumbo, carrefour, dia = map(_safe, results)
    return {"jumbo": jumbo, "carrefour": carrefour, "dia": dia, "coto": coto_items}
