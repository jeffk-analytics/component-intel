# src/cie/manufacturer/knowledge.py
"""Lookup layer over the curated knowledge file (approved design:
knowledge ships WITH the project as plain, human-editable JSON)."""
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_FILE = Path(__file__).parent / "knowledge.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads(_FILE.read_text(encoding="utf-8"))


def lookup(manufacturer: str, mpn: str) -> dict[str, Any]:
    """Resolve a manufacturer string + part number against the reference.

    Returns: canonical entry (or None), any absorbed brand implicated
    (either named in the manufacturer string, like 'Analog Devices
    Inc./Maxim Integrated', or inferred from the part-number prefix,
    like MIC -> Micrel), and any rename events.
    """
    kb = load()
    m_norm = " " + _norm(manufacturer) + " "
    entry = None
    absorbed_hit: dict[str, Any] | None = None

    for cand in kb["manufacturers"]:
        if any((" " + a + " ") in m_norm or m_norm.strip().startswith(a)
               for a in cand["aliases"]):
            entry = cand
            break
    # absorbed brand named directly in the manufacturer string?
    for cand in kb["manufacturers"]:
        for ab in cand.get("absorbed_brands", []):
            if _norm(ab["brand"]) in m_norm.strip():
                entry = entry or cand
                absorbed_hit = {**ab, "owner": cand["canonical"],
                                "evidence": "named in manufacturer string"}
    # heritage via part-number prefix (longest prefix wins)
    if absorbed_hit is None:
        upper = mpn.upper()
        for prefix in sorted(kb["mpn_prefix_heritage"], key=len, reverse=True):
            brand = kb["mpn_prefix_heritage"][prefix]
            if brand and upper.startswith(prefix):
                for cand in kb["manufacturers"]:
                    for ab in cand.get("absorbed_brands", []):
                        if ab["brand"] == brand:
                            absorbed_hit = {**ab, "owner": cand["canonical"],
                                            "evidence": f"part-number prefix "
                                                        f"'{prefix}'"}
                            entry = entry or cand
                break
    return {
        "entry": entry,
        "canonical": entry["canonical"] if entry else None,
        "absorbed": absorbed_hit,
        "renames": (entry or {}).get("renames", []),
    }
