"""Matching de un ítem de la lista contra los resultados de una tienda."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

STOP = {
    "de", "del", "la", "el", "los", "las", "con", "sin", "x", "por", "y", "en", "a", "al",
    "para", "un", "una", "u", "unidad", "unidades",
}
_UNIT = re.compile(r"^\d+(\.\d+)?(lts?|l|kgs?|kg|grs?|gr|g|ml|cc|mts?|m|un|unid)$")

MIN_SCORE = 0.25


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    s = re.sub(r"(\d)[.,](\d)", r"\1.\2", s)  # 2,25 -> 2.25
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(lts?|kgs?|grs?|ml|cc|mts?|g|kg|l)\b", r"\1\2", s)  # "500 g" -> "500g"
    s = re.sub(r"[^\w\s.]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _stem(t: str) -> str:
    """Singular/plural simple: huevos ~ huevo, galletitas ~ galletita."""
    if len(t) > 4 and t.endswith("es") and t[-3] in "lnrdz":
        return t[:-2]
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def _canon_unit(t: str) -> str:
    return re.sub(r"(lts|lt)$", "l", re.sub(r"(kgs)$", "kg", re.sub(r"(grs|gr)$", "g", t)))


def _tokens(s: str) -> List[str]:
    out = []
    for t in _norm(s).split():
        t = t.strip(".")
        if not t or t in STOP:
            continue
        out.append(_canon_unit(t) if _UNIT.match(t) else _stem(t))
    return out


def _split(tokens: List[str]):
    sizes = {t for t in tokens if _UNIT.match(t) or t.replace(".", "").isdigit()}
    words = {t for t in tokens if t not in sizes}
    return words, sizes


def _score(query: str, title: str) -> Optional[float]:
    qw, qs = _split(_tokens(query))
    tw, ts = _split(_tokens(title))
    if not qw:
        return None
    covered = len(qw & tw) / len(qw)
    # consultas cortas ("azucar ledesma") exigen todas las palabras; las largas toleran alguna menos
    if covered < (1.0 if len(qw) <= 2 else 0.6):
        return None
    jaccard = len(qw & tw) / len(qw | tw)
    size_bonus = 0.15 if qs and (qs & ts) else 0.0
    return covered * 0.6 + jaccard * 0.25 + size_bonus


def _price(r: Dict) -> float:
    try:
        return float(str(r.get("price")).replace("ARS", "").strip())
    except ValueError:
        return float("inf")


def find_best_match_for_item(item_name: str, results: List[Dict]) -> Optional[Dict]:
    """Mejor resultado para el ítem, o None si ninguno se parece lo suficiente.
    A igual calidad de match gana el más barato."""
    best, best_score = None, -1.0
    for r in results or []:
        if not (r.get("title") or "").strip() or not r.get("price"):
            continue
        sc = _score(item_name, r["title"])
        if sc is None:
            continue
        if sc > best_score + 1e-9 or (abs(sc - best_score) <= 1e-9 and best and _price(r) < _price(best)):
            best, best_score = r, sc
    return best if best is not None and best_score >= MIN_SCORE else None
