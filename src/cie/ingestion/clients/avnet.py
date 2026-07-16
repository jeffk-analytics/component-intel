# src/cie/ingestion/clients/avnet.py
"""Avnet adapter — OPTIONAL enrichment, approval-gated, DISABLED by default.

STATUS: the REQUEST half is now built against Avnet's published schema for
the customer-price v1 API (verified from the portal's application/json
request spec, 2026-07): POST a JSON body with an items[] array using
searchType=REQUEST_PART and searchTerm=<MPN>. Account-number fields are
omitted — Avnet defaults them from the subscription profile.

The RESPONSE half is built against the customer-price v1 response schema
(verified from the portal docs, 2026-07), including the '777-Submit for
Lead Time' sentinel guard and multi-quote handling.

STILL PENDING (VERIFY on first live call):
  * Subscription-key header name confirmed as Ocp-Apim-Subscription-Key
    from the portal's Try-It console; token request exact fields/scope
    come from the portal Profile page after approval.
"""
import logging
import time
from decimal import Decimal
from typing import Any, ClassVar

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cie.ingestion.clients.base import DistributorClient
from cie.ingestion.clients.common import is_retryable, map_packaging
from cie.models.enums import LeadTimeSemantics, OfferSource
from cie.models.part import DistributorOffer, PriceBreak

logger = logging.getLogger(__name__)


class AvnetClient(DistributorClient):
    distributor_id: ClassVar[str] = "avnet"
    display_name: ClassVar[str] = "Avnet"

    _token: str | None = None
    _token_expiry: float = 0.0

    @property
    def is_enabled(self) -> bool:
        return self.settings.avnet_enabled

    def _get_token(self) -> str:
        """OAuth2 client-credentials exchange against the portal's Token URL.
        VERIFY: exact required fields/scope per the portal Profile page."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = httpx.post(
            self.settings.avnet_token_url or "",
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.avnet_client_id,
                "client_secret": self.settings.avnet_client_secret,
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch_offers(self, mpn: str) -> list[DistributorOffer]:
        # Request shape per Avnet's published customer-price v1 schema.
        # Account numbers intentionally omitted: profile defaults apply.
        # Quantity omitted: defaults to the material's minimum purchase qty.
        payload = {
            "pageNum": 1,
            "pageRows": 10,
            "stock": "Y",
            "price": "Y",
            "items": [
                {
                    "itemId": 1,
                    "searchType": "REQUEST_PART",
                    "searchTerm": mpn,
                }
            ],
        }
        resp = httpx.post(
            self.settings.avnet_price_url or "",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                # VERIFY header name (Azure APIM standard assumed):
                "Ocp-Apim-Subscription-Key": self.settings.avnet_subscription_key or "",
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        return self._parse(resp.json(), mpn)

    def _parse(self, body: dict[str, Any], mpn: str) -> list[DistributorOffer]:
        """Parse a customer-price v1 reply (schema verified 2026-07).

        One request line can return MULTIPLE quotes (Avnet direct channel,
        storefront, marketplace partners like Rochester) — each becomes its
        own offer. Avnet's Part Matching may substitute a 'better' part;
        we accept only exact matches to the requested MPN, per the design
        rule that disambiguation happens once, on Nexar data."""
        for msg in body.get("messages") or []:
            mtype = (msg.get("type") or "").upper()
            if mtype == "E":
                logger.warning("Avnet error for %s [%s]: %s",
                               mpn, msg.get("msgCd"), msg.get("message"))
            elif mtype == "W":
                logger.info("Avnet warning for %s [%s]: %s",
                            mpn, msg.get("msgCd"), msg.get("message"))
        offers: list[DistributorOffer] = []
        for item in body.get("items") or []:
            quoted = (item.get("quotedPartNumber") or "").strip()
            if quoted.upper() != mpn.strip().upper():
                continue
            comments = (item.get("comments") or "").lower()
            if "part not found" in comments:
                continue
            offers.append(self._parse_item(item))
        return offers

    @staticmethod
    def _lead_weeks_to_days(raw: Any) -> int | None:
        """Avnet reports factory lead time as a STRING of weeks — except when
        it's a sentinel like '777-Submit for Lead Time', meaning 'not stated'.
        Guard rules: non-numeric -> None; the 777 sentinel -> None; any
        implausible value (>= 500 weeks) -> None. Never invent."""
        if raw is None:
            return None
        text = str(raw).strip()
        if not text or not text.replace(".", "", 1).isdigit():
            return None
        weeks = float(text)
        if weeks <= 0 or weeks == 777 or weeks >= 500:
            return None
        return round(weeks * 7)

    def _parse_item(self, item: dict[str, Any]) -> DistributorOffer:
        supplier = (item.get("quotedSupplierName") or "").strip()
        lead_raw = item.get("factoryLeadTimeWks")
        lead_days = self._lead_weeks_to_days(lead_raw)
        price = item.get("price")
        sell_qty = item.get("sellQuantity")
        breaks: list[PriceBreak] = []
        if price is not None:
            # Price is quoted FOR the sellQuantity, so that's its break qty.
            breaks.append(PriceBreak(
                quantity=int(sell_qty) if sell_qty else 1,
                unit_price=Decimal(str(price)),
                currency=item.get("currency") or "USD",
            ))
        pkg_raw = item.get("packageDescription") or item.get("packageTypeCode")
        return DistributorOffer(
            distributor_id=self.distributor_id,
            distributor_name=(f"Avnet ({supplier})" if supplier else "Avnet"),
            sku=item.get("erpPartNumber") or None,
            source=OfferSource.DIRECT_API,
            authorized_seller=True,
            stock_qty=max(int(item.get("inStock") or 0), 0),
            moq=int(item["minimumQuantity"]) if item.get("minimumQuantity") else None,
            order_multiple=(int(item["multipleQuantity"])
                            if item.get("multipleQuantity") else None),
            packaging=map_packaging(pkg_raw),
            packaging_raw=pkg_raw,
            price_breaks=breaks,
            lead_time_days=lead_days,
            lead_time_semantics=(
                LeadTimeSemantics.FACTORY if lead_days is not None
                else LeadTimeSemantics.UNSPECIFIED
            ),
            lead_time_raw=str(lead_raw) if lead_raw is not None else None,
            buy_url=None,   # response carries no product URL
        )
