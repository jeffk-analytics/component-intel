# src/cie/ingestion/clients/arrow.py
"""Arrow adapter — OPTIONAL enrichment, approval-gated, disabled without keys.

VERIFY BEFORE WIRING: I am NOT confident of Arrow's current developer API
surface. The itemservice/v4 search shape below reflects their historical
public API; confirm endpoint, auth params, and response fields against the
docs you receive after registration approval. Until keys exist this adapter
is inert and the pipeline runs without it."""
from decimal import Decimal
from typing import Any, ClassVar

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cie.ingestion.clients.base import DistributorClient
from cie.ingestion.clients.common import map_packaging, is_retryable
from cie.ingestion.leadtime import parse_lead_time_to_days
from cie.models.enums import LeadTimeSemantics, OfferSource
from cie.models.part import DistributorOffer, PriceBreak

_SEARCH_URL = "https://api.arrow.com/itemservice/v4/en/search/token"  # VERIFY


class ArrowClient(DistributorClient):
    distributor_id: ClassVar[str] = "arrow"
    display_name: ClassVar[str] = "Arrow"

    @property
    def is_enabled(self) -> bool:
        return self.settings.arrow_enabled

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch_offers(self, mpn: str) -> list[DistributorOffer]:
        resp = httpx.get(
            _SEARCH_URL,
            params={
                "login": self.settings.arrow_login,
                "apikey": self.settings.arrow_api_key,
                "search_token": mpn,
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        return self._parse(resp.json(), mpn)

    def _parse(self, body: dict[str, Any], mpn: str) -> list[DistributorOffer]:
        offers: list[DistributorOffer] = []
        # Path per current docs (verified 2026-07):
        #   itemserviceresult -> data[] -> PartList[] -> InvOrg -> sources[]
        # Some responses nest a further "sourceParts" list per source; handle both.
        result = body.get("itemserviceresult") or {}
        for data in result.get("data") or []:
            for part in data.get("PartList") or []:
                if (part.get("partNum") or "").upper() != mpn.upper():
                    continue
                inv_org = part.get("InvOrg") or {}
                sources = inv_org.get("sources") or []
                # Legacy/alternate nesting seen in older payloads:
                for site in inv_org.get("webSites") or []:
                    sources.extend(site.get("sources") or [])
                for source in sources:
                    nested = source.get("sourceParts")
                    for sp in (nested if nested else [source]):
                        offers.append(self._parse_source_part(sp))
        return offers

    def _parse_source_part(self, sp: dict[str, Any]) -> DistributorOffer:
        prices_obj = sp.get("Prices") or {}
        # Docs show "ResaleList"; accept either casing defensively.
        prices = prices_obj.get("ResaleList") or prices_obj.get("resaleList") or []
        avail = (sp.get("Availability") or [{}])[0]                # VERIFY
        lead_raw = sp.get("leadTime")                              # VERIFY
        lead_days = parse_lead_time_to_days(
            (lead_raw or {}).get("supplierLeadTime") if isinstance(lead_raw, dict)
            else lead_raw
        )
        pkg_raw = sp.get("packagingType")                          # VERIFY
        return DistributorOffer(
            distributor_id=self.distributor_id,
            distributor_name=self.display_name,
            sku=sp.get("sourcePartId"),
            source=OfferSource.DIRECT_API,
            authorized_seller=True,
            stock_qty=max(int(avail.get("fohQty") or 0), 0),       # VERIFY
            moq=sp.get("minimumOrderQuantity") or None,
            order_multiple=sp.get("multipleOrderQuantity") or None,
            packaging=map_packaging(pkg_raw),
            packaging_raw=pkg_raw,
            price_breaks=[
                PriceBreak(
                    quantity=int(p["minQty"]),
                    unit_price=Decimal(str(p["displayPrice"])),
                    currency=p.get("currency") or "USD",
                )
                for p in prices if p.get("minQty") and p.get("displayPrice")
            ],
            lead_time_days=lead_days,
            lead_time_semantics=(
                LeadTimeSemantics.DISTRIBUTOR if lead_days is not None
                else LeadTimeSemantics.UNSPECIFIED
            ),
            lead_time_raw=str(lead_raw) if lead_raw is not None else None,
            buy_url=sp.get("buyUrl") or None,                      # VERIFY
        )
