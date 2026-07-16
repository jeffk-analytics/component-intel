# src/cie/ingestion/clients/oemsecrets.py
"""OEMsecrets Part Search API — the new aggregation SPINE (Path B decision,
2026-07). Free API covering 140+ distributors incl. Arrow/Avnet listings.

Role: MPN resolution + aggregated multi-distributor offers. When no
OEMsecrets key is configured, the pipeline falls back to the Digi-Key
spine (free direct API). Nexar has been removed from the architecture.

REQUEST shape (VERIFY on first live call — community-documented pattern):
    GET https://oemsecretsapi.com/partsearch
        ?apiKey=<key>&searchTerm=<mpn>&countryCode=US&currencyCode=USD

RESPONSE parsing: PENDING the documentation example (the person is
harvesting it, same playbook as the Avnet adapter). _parse raises
NotImplementedError until then, so this spine cannot half-work silently.
"""
import logging
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from decimal import Decimal

from cie.cache.store import CacheStore
from cie.config import Settings
from cie.ingestion.clients.common import (SpineCandidate, is_retryable,
                                          map_packaging, normalize_url)
from cie.models.enums import OfferSource
from cie.models.part import DistributorOffer, PriceBreak

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://oemsecretsapi.com/partsearch"  # VERIFY


class OemSecretsClient:
    """Spine client: same interface shape as NexarClient.search()."""

    def __init__(self, settings: Settings, cache: CacheStore) -> None:
        self.settings = settings
        self.cache = cache

    @property
    def is_enabled(self) -> bool:
        return self.settings.oemsecrets_enabled

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch(self, mpn: str) -> dict[str, Any]:
        resp = httpx.get(
            _SEARCH_URL,
            params={
                "apiKey": self.settings.oemsecrets_api_key,
                "searchTerm": mpn,
                "countryCode": "US",     # VERIFY param names
                "currencyCode": "USD",   # VERIFY
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def search(self, mpn: str) -> tuple[list[SpineCandidate], datetime]:
        """Resolve an MPN into candidates + aggregated offers, cached with
        the same static/availability split as the Nexar spine."""
        norm = mpn.strip().upper()
        static_key = f"oemsecrets:static:{norm}"
        avail_key = f"offers:oemsecrets:{norm}"

        raw = self.cache.get_static(static_key)
        cached_avail = self.cache.get_availability(avail_key)
        if raw is None or cached_avail is None:
            raw = self._fetch(norm)
            self.cache.set_static(static_key, raw)
            fetched_at = self.cache.set_availability(avail_key, raw)
        else:
            raw, fetched_at = cached_avail
        return self._parse(raw, norm), fetched_at

    def _parse(self, body: dict[str, Any], mpn: str) -> list[SpineCandidate]:
        """Built against a live specimen (v3.0 response, 2026-07).
        Shape: {"version", "status", "search_term", "parts_returned",
        "stock": [one row per distributor offer]}.

        Specimen-taught rules:
          * lead_time changes units per row (lead_time_format says which);
            lead_time_weeks is always weeks -> preferred.
          * prices contain placeholder rows (0.0000 @ break 0) -> skipped.
          * compliance.rohs is a bool where false means NOT STATED, not
            non-compliant -> only true becomes evidence.
          * distributor rows for houses we query directly (Digi-Key,
            Mouser, Arrow, Avnet) get canonical ids so the existing
            dedup lets direct data win.
        """
        rows = body.get("stock") or []
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for row in rows:
            pn = (row.get("part_number") or "").strip()
            if not pn:
                continue
            key = pn.upper()
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        candidates: list[SpineCandidate] = []
        for key in order:
            grp = groups[key]
            pn = (grp[0].get("part_number") or "").strip()
            mfr = next((r.get("manufacturer") for r in grp
                        if r.get("manufacturer")), "Unknown")
            # longest description tends to be the informative one
            desc = max((r.get("description") or "" for r in grp), key=len)
            datasheet = next((r.get("datasheet_url") for r in grp
                              if r.get("datasheet_url")), None)
            life_raw = next((r.get("life_cycle") for r in grp
                             if r.get("life_cycle")), None)
            compliance: dict[str, str] = {}
            if any((r.get("compliance") or {}).get("rohs") is True
                   for r in grp):
                compliance["rohs"] = "RoHS Compliant"
            candidates.append(SpineCandidate(
                source_part_id=pn,
                mpn=pn,
                manufacturer=str(mfr),
                description=desc or None,
                category_name=None,     # not provided; classifier falls
                category_path=None,     # back to description keywords
                factory_lead_days=None,
                datasheet_url=normalize_url(datasheet),
                lifecycle_status_raw=life_raw,
                compliance=compliance,
                offers=[o for o in (self._row_offer(r) for r in grp)
                        if o is not None],
            ))
        return candidates

    @staticmethod
    def _row_offer(row: dict[str, Any]) -> DistributorOffer | None:
        dist = row.get("distributor") or {}
        name = (dist.get("distributor_name") or "").strip()
        if not name:
            return None
        low = name.lower()
        if "digi-key" in low or "digikey" in low:
            dist_id = "digikey"
        elif "mouser" in low:
            dist_id = "mouser"
        elif "arrow" in low:
            dist_id = "arrow"
        elif "avnet" in low:
            dist_id = "avnet"
        else:
            dist_id = f"oem-{dist.get('distributor_id') or low.replace(' ', '-')}"

        lead_days = None
        weeks = row.get("lead_time_weeks")
        if isinstance(weeks, (int, float)) and weeks > 0:
            lead_days = int(weeks) * 7
        else:
            lt = row.get("lead_time")
            fmt = (row.get("lead_time_format") or "").strip().lower()
            if isinstance(lt, (int, float)) and lt > 0:
                lead_days = int(lt) * 7 if fmt == "weeks" else int(lt)

        breaks = []
        for cur, plist in (row.get("prices") or {}).items():
            for pb in plist or []:
                try:
                    qty = int(pb.get("unit_break") or 0)
                    price = Decimal(str(pb.get("unit_price")))
                except (TypeError, ValueError, ArithmeticError):
                    continue
                if qty < 1 or price <= 0:
                    continue    # placeholder rows (0.0000 @ 0) are noise
                breaks.append(PriceBreak(quantity=qty, unit_price=price,
                                         currency=cur))

        moq_raw = row.get("moq")
        moq = (int(moq_raw) if isinstance(moq_raw, (int, float))
               and moq_raw >= 1 else None)
        stock = row.get("quantity_in_stock")
        stock = int(stock) if isinstance(stock, (int, float)) else 0
        sku_raw = row.get("sku")
        sku = (str(sku_raw).strip() or None) if sku_raw not in (None, "") else None
        return DistributorOffer(
            distributor_id=dist_id,
            distributor_name=name,
            sku=sku,
            source=OfferSource.OEMSECRETS_AGGREGATED,
            stock_qty=max(stock, 0),
            moq=moq,
            packaging=map_packaging(row.get("packaging")),
            packaging_raw=(row.get("packaging") or None),
            price_breaks=breaks,
            lead_time_days=lead_days,
            lead_time_raw=str(row.get("source_lead_time") or "") or None,
            buy_url=normalize_url(row.get("buy_now_url")),
        )
