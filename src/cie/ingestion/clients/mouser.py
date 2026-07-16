# src/cie/ingestion/clients/mouser.py
"""Mouser Search API adapter. Simple API-key auth (key as query parameter).
The response's Availability and LeadTime fields are free-text strings —
everything goes through the lead-time parser, nothing is assumed."""
import re
from decimal import Decimal
from typing import Any, ClassVar

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cie.ingestion.clients.base import DistributorClient
from cie.ingestion.clients.common import map_packaging, is_retryable
from cie.ingestion.leadtime import parse_lead_time_to_days
from cie.models.enums import LeadTimeSemantics, OfferSource
from cie.models.part import DistributorOffer, IncomingStock, PriceBreak

_SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"
_STOCK_RE = re.compile(r"(\d[\d,]*)")
_PRICE_RE = re.compile(r"[\d.]+")


def _moq(raw) -> int | None:
    """Mouser sometimes reports Min as 0 or a non-numeric string; a
    'minimum of zero' is not a fact — record unknown instead."""
    try:
        v = int(str(raw).replace(",", "").strip())
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


class MouserClient(DistributorClient):
    distributor_id: ClassVar[str] = "mouser"
    display_name: ClassVar[str] = "Mouser"

    @property
    def is_enabled(self) -> bool:
        return self.settings.mouser_enabled

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch_offers(self, mpn: str) -> list[DistributorOffer]:
        resp = httpx.post(
            _SEARCH_URL,
            params={"apiKey": self.settings.mouser_api_key},
            json={"SearchByPartRequest": {"mouserPartNumber": mpn}},
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        parts = ((body.get("SearchResults") or {}).get("Parts")) or []
        # Mouser returns near-matches too; keep only exact MPN matches —
        # variant selection is the disambiguator's job, done on Nexar data.
        exact = [p for p in parts
                 if (p.get("ManufacturerPartNumber") or "").upper() == mpn.upper()]
        return [self._parse(p) for p in exact]

    @staticmethod
    def _parse_stock(availability: str | None) -> int:
        """'9,331 In Stock' -> 9331; 'None'/absent -> 0."""
        if not availability:
            return 0
        m = _STOCK_RE.search(availability)
        return int(m.group(1).replace(",", "")) if m else 0

    def _parse(self, p: dict[str, Any]) -> DistributorOffer:
        breaks = []
        for pb in p.get("PriceBreaks") or []:
            m = _PRICE_RE.search(pb.get("Price") or "")
            if m:
                breaks.append(PriceBreak(
                    quantity=int(pb["Quantity"]),
                    unit_price=Decimal(m.group(0)),
                    currency=pb.get("Currency") or "USD",
                ))
        lead_raw = p.get("LeadTime")
        lead_days = parse_lead_time_to_days(lead_raw)
        incoming = []
        on_order = p.get("AvailabilityOnOrder")  # VERIFY field name/shape
        if on_order:
            try:
                incoming.append(IncomingStock(quantity=int(str(on_order).replace(",", ""))))
            except ValueError:
                pass
        return DistributorOffer(
            distributor_id=self.distributor_id,
            distributor_name=self.display_name,
            sku=p.get("MouserPartNumber"),
            source=OfferSource.DIRECT_API,
            authorized_seller=True,
            stock_qty=self._parse_stock(p.get("Availability")),
            moq=_moq(p.get("Min")),
            order_multiple=int(p["Mult"]) if p.get("Mult") else None,
            packaging=map_packaging(None),   # Mouser search doesn't state it cleanly
            packaging_raw=None,
            price_breaks=breaks,
            lead_time_days=lead_days,
            # Mouser doesn't label its LeadTime as factory vs distributor:
            lead_time_semantics=LeadTimeSemantics.UNSPECIFIED,
            lead_time_raw=lead_raw,
            incoming=incoming,
            buy_url=p.get("ProductDetailUrl") or None,
        )
