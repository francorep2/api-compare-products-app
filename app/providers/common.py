from __future__ import annotations

import re
from typing import Optional

BARCODE_RE = re.compile(r"^\d{8,14}$")


def only_digits(s: object) -> Optional[str]:
    d = re.sub(r"\D", "", str(s)) if s is not None else ""
    return d or None


def fmt_price(val: object) -> Optional[str]:
    """Formato público de precio: 'ARS 1234.00'."""
    try:
        f = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f"ARS {f:.2f}" if f > 0 else None


def short_promo(text: Optional[str]) -> Optional[str]:
    """Reduce textos de promo a '2x1', '30% OFF', '2da al 70%'..."""
    if not text:
        return None
    t = " ".join(str(text).split()).lower()
    m = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", t)
    if m:
        return f"{m.group(1)}x{m.group(2)}"
    m = re.search(r"(\d{1,3})\s*%", t)
    if m:
        pct = m.group(1)
        if any(w in t for w in ("2da", "segunda", "llevando 2", "2do")):
            return f"2da al {pct}%"
        return f"{pct}% OFF"
    return None
