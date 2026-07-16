# src/cie/ingestion/clients/common.py
"""Shared plumbing for all source clients — packaging mapping, retry
predicate, and the spine candidate structure. (Extracted from the removed
Nexar client when Nexar was erased from the architecture, 2026-07.)"""
from dataclasses import dataclass, field

import httpx

from cie.models.enums import Packaging
from cie.models.part import DistributorOffer

_PACKAGING_MAP = {
    "cut tape": Packaging.CUT_TAPE, "cut tape (ct)": Packaging.CUT_TAPE,
    "reel": Packaging.REEL, "tape & reel": Packaging.TAPE_AND_REEL,
    "tape & reel (tr)": Packaging.TAPE_AND_REEL,
    "tape and reel": Packaging.TAPE_AND_REEL,
    "tray": Packaging.TRAY, "tube": Packaging.TUBE,
    "bulk": Packaging.BULK, "bag": Packaging.BAG,
}


def normalize_url(raw: str | None) -> str | None:
    """Complete protocol-relative URLs (Digi-Key writes '//mm.digikey.com/...'
    without the https: prefix — browsers tolerate it, strict validation
    doesn't). None/blank stays None; anything else passes through."""
    if not raw:
        return None
    u = str(raw).strip()
    if u.startswith("//"):
        return "https:" + u
    return u or None


def map_packaging(raw: str | None) -> Packaging:
    """Upstream packaging string -> Packaging enum (UNKNOWN if unmapped)."""
    if not raw:
        return Packaging.UNKNOWN
    return _PACKAGING_MAP.get(raw.strip().lower(), Packaging.UNKNOWN)


def is_retryable(exc: BaseException) -> bool:
    """Retry only on rate limits and server errors."""
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


@dataclass
class SpineCandidate:
    """One resolved part candidate from whichever spine is active:
    identity + static data + that spine's offers."""
    source_part_id: str                 # spine's stable handle for the part
    mpn: str
    manufacturer: str
    description: str | None
    category_name: str | None
    category_path: str | None
    factory_lead_days: int | None
    datasheet_url: str | None
    lifecycle_status_raw: str | None = None   # e.g. Digi-Key ProductStatus
    parametrics: dict[str, str] = field(default_factory=dict)
    compliance: dict[str, str] = field(default_factory=dict)  # rohs/reach/eccn/msl
    offers: list[DistributorOffer] = field(default_factory=list)
