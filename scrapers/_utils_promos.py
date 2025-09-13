# scrapers/_utils_promos.py
import re
from typing import Optional
from playwright.sync_api import Locator

PROMO_NORMALIZER = re.compile(
    r"""
    (?:
      (\d{1,3}\s*%?\s*off) |        # 50% OFF / 50 OFF
      (\d\s*x\s*\d) |               # 2x1, 3x2, 4x3
      (segunda\s+al\s+\d{1,3}\s*%) |# Segunda al 70%
      (lleva\s+\d+\s+y\s+paga\s+\d+)# lleva 3 y paga 2
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

BADGE_SELECTORS = [
    "[class*='flagName']",
    "[class*='flagText']",
    "[class*='productHighlightText']",
    "[class*='discount']",
    "[data-testid*='discount']",
    "[data-testid*='badge']",
]

def _pick_best_phrase(text: str) -> Optional[str]:
    # Tomá la frase “clave” (2x1, 3x2, 50% OFF, etc.). Si no matchea, devolvé texto limpio.
    m = PROMO_NORMALIZER.search(text)
    if m:
        return m.group(0).upper().replace("  ", " ").strip()
    clean = re.sub(r"\s+", " ", text).strip()
    return clean if clean else None

async def extract_promotion(page) -> Optional[str]:
    selectors = [
        "[class*='promo']", "[class*='flag']", "[class*='badge']",
        "span:has-text('%')", "span:has-text('x')"
    ]
    for sel in selectors:
        els = page.locator(sel)
        if await els.count():
            texts = [t.strip() for t in await els.all_inner_texts() if t.strip()]
            if texts:
                return " | ".join(dict.fromkeys(texts))
    return None
