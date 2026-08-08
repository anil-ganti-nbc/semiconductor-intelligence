"""Known-hardware canonicalization + seed data (M5, DIFF_ENGINE.md §4).

canonicalize() turns vendor marketing strings into stable slugs:
    "AMD Ryzen™ AI MAX+ 395 (Strix Halo)" -> "ryzen-ai-max+-395"
    "Intel® Celeron® N5095 Processor"     -> "celeron-n5095"
    "AMD Ryzen9 6900HX" and "AMD Ryzen 9 6900HX" -> both "ryzen-9-6900hx"

A component whose slug is absent from the components table triggers the
platform's highest-severity signal (unseen_component). Canonicalization
failures keep the raw string and are flagged, never guessed.

Consistency rule (learned the hard way): the SEED list below is stored as
RAW vendor strings and canonicalized through THIS SAME function at seed time,
so seed slugs can never drift from runtime slugs. Never hand-write slugs.
"""

from __future__ import annotations

import re

_STRIP_TOKENS = re.compile(
    r"®|™|\(r\)|\(tm\)|\b(amd|intel|nvidia|qualcomm|apple|processor|cpu|graphics|mobile)\b",
    re.IGNORECASE,
)
_PARENS = re.compile(r"\([^)]*\)")
_NON_SLUG = re.compile(r"[^a-z0-9+\-. ]")
# Collapse the "Ryzen9" / "Ryzen 9" spelling split: family name glued to its
# tier digit becomes separated, so both spellings yield the same slug.
_FAMILY_SPLIT = re.compile(r"\b(ryzen|core|radeon|arc|geforce|rtx|gtx|xe)(\d)")
_WS = re.compile(r"\s+")


def canonicalize(raw: str) -> str | None:
    """Slug for a component string; None if nothing meaningful survives."""
    s = _PARENS.sub(" ", raw)
    s = _STRIP_TOKENS.sub(" ", s)
    s = _NON_SLUG.sub(" ", s.lower())
    s = _FAMILY_SPLIT.sub(r"\1 \2", s)
    s = _WS.sub(" ", s).strip()
    if not s or not re.search(r"\d", s):  # a component slug without digits is junk
        return None
    return s.replace(" ", "-")


# Curated seed as RAW vendor strings (canonicalized at seed time). Covers the
# common boutique-mini-PC silicon as of mid-2026. Grows automatically at
# runtime (source='discovered'); the human can also mark discoveries 'seen'
# from the dashboard. This list only decides what is NOT breaking news on a
# fresh database's first crawl.
SEED_COMPONENTS: list[tuple[str, str]] = [  # (kind, raw vendor string)
    # Intel low power / Atom-class
    ("cpu", "Intel Celeron N5095"), ("cpu", "Intel Celeron N5105"),
    ("cpu", "Intel N95"), ("cpu", "Intel N97"), ("cpu", "Intel N100"),
    ("cpu", "Intel N150"), ("cpu", "Intel N250"), ("cpu", "Intel Core i3-N305"),
    ("cpu", "Intel Core i3-N355"), ("cpu", "Intel Processor N200"),
    # Intel Core H
    ("cpu", "Intel Core i5-1235U"), ("cpu", "Intel Core i5-12450H"),
    ("cpu", "Intel Core i5-12600H"), ("cpu", "Intel Core i7-12650H"),
    ("cpu", "Intel Core i7-13620H"), ("cpu", "Intel Core i9-12900H"),
    ("cpu", "Intel Core i9-13900H"), ("cpu", "Intel Core i9-13900HK"),
    ("cpu", "Intel Core i9-13900HX"), ("cpu", "Intel Core i9-14900HX"),
    # Intel Core Ultra
    ("cpu", "Intel Core Ultra 5 125H"), ("cpu", "Intel Core Ultra 5 225H"),
    ("cpu", "Intel Core Ultra 7 155H"), ("cpu", "Intel Core Ultra 7 255H"),
    ("cpu", "Intel Core Ultra 7 356H"), ("cpu", "Intel Core Ultra 9 185H"),
    ("cpu", "Intel Core Ultra 9 285H"), ("cpu", "Intel Core Ultra 9 285HX"),
    ("cpu", "Intel Core Ultra 9 275HX"), ("cpu", "Intel Core Ultra 7 255HX"),
    ("cpu", "Intel Core Ultra 5 245HX"),
    # AMD mainstream APUs
    ("cpu", "AMD Ryzen 5 3500U"), ("cpu", "AMD Ryzen 5 5500U"),
    ("cpu", "AMD Ryzen 7 5700U"), ("cpu", "AMD Ryzen 5 5560U"),
    ("cpu", "AMD Ryzen 7 5825U"), ("cpu", "AMD Ryzen 5 6600H"),
    ("cpu", "AMD Ryzen 7 6800H"), ("cpu", "AMD Ryzen 7 PRO 6850U"),
    ("cpu", "AMD Ryzen 5 PRO 6650H"), ("cpu", "AMD Ryzen 9 6900HX"),
    ("cpu", "AMD Ryzen 5 7540U"), ("cpu", "AMD Ryzen 5 7545U"),
    ("cpu", "AMD Ryzen 7 7735HS"), ("cpu", "AMD Ryzen 7 7840HS"),
    ("cpu", "AMD Ryzen 5 7640HS"), ("cpu", "AMD Ryzen 7 7435HS"),
    ("cpu", "AMD Ryzen 9 7940HS"), ("cpu", "AMD Ryzen 9 7940HX"),
    ("cpu", "AMD Ryzen 7 8745H"), ("cpu", "AMD Ryzen 7 8745HS"),
    ("cpu", "AMD Ryzen 7 8845HS"), ("cpu", "AMD Ryzen 7 PRO 8845HS"),
    ("cpu", "AMD Ryzen 9 8945HS"), ("cpu", "AMD Ryzen 9 8945HX"),
    ("cpu", "AMD Ryzen 9 9955HX"),
    ("cpu", "AMD Ryzen 5 H 235"), ("cpu", "AMD Ryzen 7 H 255"),
    ("cpu", "AMD Ryzen 9 H 365"),
    # Bare families (stores that list only the family, no model number) — seeded
    # so a family-only CPU reads as known, never a false "unseen silicon" alarm.
    ("cpu", "Intel Core Ultra 3"), ("cpu", "Intel Core Ultra 5"),
    ("cpu", "Intel Core Ultra 7"), ("cpu", "Intel Core Ultra 9"),
    ("cpu", "Intel Core 3 100U"), ("cpu", "Intel Core 5"), ("cpu", "Intel Core 7"),
    ("cpu", "AMD Ryzen 3"), ("cpu", "AMD Ryzen 5"), ("cpu", "AMD Ryzen 7"),
    ("cpu", "AMD Ryzen 9"), ("cpu", "AMD Ryzen AI 5"), ("cpu", "AMD Ryzen AI 7"),
    ("cpu", "AMD Ryzen AI 9"),
    # AMD AI / halo
    ("cpu", "AMD Ryzen AI 7 350"), ("cpu", "AMD Ryzen AI 9 365"),
    ("cpu", "AMD Ryzen AI 9 HX 370"), ("cpu", "AMD Ryzen AI 9 HX 375"),
    ("cpu", "AMD Ryzen AI MAX 385"), ("cpu", "AMD Ryzen AI MAX+ 395"),
    # iGPUs / dGPUs
    ("gpu", "AMD Radeon 680M"), ("gpu", "AMD Radeon 780M"),
    ("gpu", "AMD Radeon 880M"), ("gpu", "AMD Radeon 890M"),
    ("gpu", "AMD Radeon 8050S"), ("gpu", "AMD Radeon 8060S"),
    ("gpu", "AMD Radeon RX 6600M"), ("gpu", "AMD Radeon RX 7600M"),
    ("gpu", "Intel Arc 140T"), ("gpu", "Intel Arc A770"),
    ("gpu", "NVIDIA RTX 4060"), ("gpu", "NVIDIA RTX 4070"),
    ("gpu", "NVIDIA GeForce RTX 5060"), ("gpu", "NVIDIA GeForce RTX 5070"),
    ("gpu", "NVIDIA GeForce RTX 5070 Ti"), ("gpu", "NVIDIA GeForce RTX 5080"),
    ("gpu", "NVIDIA GeForce RTX 5090"),
]
