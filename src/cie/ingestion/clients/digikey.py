# src/cie/ingestion/clients/digikey.py
"""Digi-Key Product Information API V4 adapter.

VERIFY BEFORE WIRING (V3 was sunset; V4 specifics move):
  * OAuth2 two-legged client-credentials token at
    https://api.digikey.com/v1/oauth2/token (production app, not sandbox).
  * Endpoint GET https://api.digikey.com/products/v4/search/{mpn}/productdetails
    and the exact response field names marked VERIFY below.
  * Required headers: X-DIGIKEY-Client-Id plus Bearer token; locale headers.
"""
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cie.ingestion.clients.base import DistributorClient
from cie.ingestion.clients.common import SpineCandidate, map_packaging, is_retryable, normalize_url
from cie.ingestion.leadtime import weeks_to_days
from cie.models.enums import LeadTimeSemantics, OfferSource
from cie.models.part import DistributorOffer, PriceBreak

_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
_DETAILS_URL = "https://api.digikey.com/products/v4/search/{mpn}/productdetails"
_KEYWORD_URL = "https://api.digikey.com/products/v4/search/keyword"  # VERIFY


class DigiKeyClient(DistributorClient):
    distributor_id: ClassVar[str] = "digikey"
    display_name: ClassVar[str] = "Digi-Key"

    _token: str | None = None
    _token_expiry: float = 0.0

    @property
    def is_enabled(self) -> bool:
        return self.settings.digikey_enabled

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.digikey_client_id,
                "client_secret": self.settings.digikey_client_secret,
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 600))
        return self._token

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch_offers(self, mpn: str) -> list[DistributorOffer]:
        resp = httpx.get(
            _DETAILS_URL.format(mpn=quote(mpn, safe="")),
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-DIGIKEY-Client-Id": self.settings.digikey_client_id or "",
                "X-DIGIKEY-Locale-Site": "US",
                "X-DIGIKEY-Locale-Currency": "USD",
            },
            timeout=self.settings.http_timeout_seconds,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return self._parse(resp.json())

    # ------------------------------------------------------------------
    # COMPLIANCE SPECIALIST ROLE: when another spine (OEMsecrets) supplies
    # identity but no compliance data, the pipeline asks Digi-Key's details
    # endpoint just for the Classifications block. Cached 7 days.
    # ------------------------------------------------------------------
    def get_compliance(self, mpn: str) -> dict[str, str]:
        norm = mpn.strip().upper()
        key = f"digikey:compliance:{norm}"
        cached = self.cache.get_static(key)
        if cached is not None:
            return cached
        body = self._fetch_details_raw(norm)
        product = (body.get("Product") or body) if isinstance(body, dict) else {}
        cls_block = product.get("Classifications") or {}
        compliance: dict[str, str] = {}
        for out_key, names in (
            ("rohs", ("RohsStatus", "RoHSStatus")),
            ("reach", ("ReachStatus", "REACHStatus")),
            ("eccn", ("ExportControlClassNumber", "Eccn")),
            ("msl", ("MoistureSensitivityLevel", "Msl")),
        ):
            for n in names:
                v = cls_block.get(n)
                if v:
                    compliance[out_key] = str(v)
                    break
        self.cache.set_static(key, compliance)
        return compliance

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _fetch_details_raw(self, mpn: str) -> dict:
        resp = httpx.get(
            _DETAILS_URL.format(mpn=quote(mpn, safe="")),
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-DIGIKEY-Client-Id": self.settings.digikey_client_id or "",
                "X-DIGIKEY-Locale-Site": "US",
                "X-DIGIKEY-Locale-Currency": "USD",
            },
            timeout=self.settings.http_timeout_seconds,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # SPINE ROLE (added when Nexar was erased): Digi-Key can also resolve
    # MPNs and supply identity, category, datasheet, parametrics, and
    # lifecycle status via its keyword search. Free, generous daily limits.
    # ------------------------------------------------------------------
    def search(self, mpn: str) -> tuple[list[SpineCandidate], datetime]:
        """Resolve an MPN into spine candidates (cached like all spines)."""
        norm = mpn.strip().upper()
        static_key = f"digikey:static:{norm}"
        avail_key = f"offers:digikey-spine:{norm}"
        raw = self.cache.get_static(static_key)
        cached = self.cache.get_availability(avail_key)
        if raw is None or cached is None:
            raw = self._keyword_search(norm)
            self.cache.set_static(static_key, raw)
            fetched_at = self.cache.set_availability(avail_key, raw)
        else:
            raw, fetched_at = cached
        return self._parse_candidates(raw), fetched_at

    @retry(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _keyword_search(self, mpn: str) -> dict[str, Any]:
        resp = httpx.post(
            _KEYWORD_URL,
            json={"Keywords": mpn, "Limit": 5, "Offset": 0},  # VERIFY body
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-DIGIKEY-Client-Id": self.settings.digikey_client_id or "",
                "X-DIGIKEY-Locale-Site": "US",
                "X-DIGIKEY-Locale-Currency": "USD",
            },
            timeout=self.settings.http_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_candidates(self, body: dict[str, Any]) -> list[SpineCandidate]:
        candidates: list[SpineCandidate] = []
        for prod in body.get("Products") or []:  # VERIFY envelope
            mfr_pn = (prod.get("ManufacturerProductNumber")
                      or prod.get("ManufacturerPartNumber") or "")  # VERIFY
            if not mfr_pn:
                continue
            category = (prod.get("Category") or {})
            desc = (prod.get("Description") or {})
            description = (desc.get("ProductDescription")
                           if isinstance(desc, dict) else prod.get("Description"))
            status = (prod.get("ProductStatus") or {})
            status_raw = (status.get("Status")
                          if isinstance(status, dict) else status) or None
            params = {}
            for pr in prod.get("Parameters") or []:  # VERIFY field names
                name = pr.get("ParameterText") or pr.get("Parameter")
                val = pr.get("ValueText") or pr.get("Value")
                if name and val is not None:
                    params[str(name)] = str(val)
            cls_block = prod.get("Classifications") or {}   # VERIFY names
            compliance = {}
            for key, names in (
                ("rohs", ("RohsStatus", "RoHSStatus")),
                ("reach", ("ReachStatus", "REACHStatus")),
                ("eccn", ("ExportControlClassNumber", "Eccn")),
                ("msl", ("MoistureSensitivityLevel", "Msl")),
            ):
                for n in names:
                    v = cls_block.get(n)
                    if v:
                        compliance[key] = str(v)
                        break
            candidates.append(SpineCandidate(
                source_part_id=str(prod.get("ProductVariations", [{}])[0]
                                   .get("DigiKeyProductNumber", mfr_pn)),
                mpn=mfr_pn,
                manufacturer=((prod.get("Manufacturer") or {}).get("Name")
                              or "Unknown"),
                description=description,
                category_name=category.get("Name"),
                category_path=category.get("Name"),
                factory_lead_days=weeks_to_days(prod.get("ManufacturerLeadWeeks")),
                datasheet_url=normalize_url(prod.get("DatasheetUrl")),
                lifecycle_status_raw=status_raw,
                parametrics=params,
                compliance=compliance,
                offers=self._parse(prod),
            ))
        return candidates

    def _parse(self, body: dict[str, Any]) -> list[DistributorOffer]:
        product = body.get("Product") or body  # VERIFY envelope shape
        lead_days = weeks_to_days(product.get("ManufacturerLeadWeeks"))  # VERIFY
        product_url = normalize_url(product.get("ProductUrl"))
        offers: list[DistributorOffer] = []
        # V4 models one product as N "variations" (cut tape / reel / tray),
        # each a distinct purchasable position — exactly our offer concept.
        for var in product.get("ProductVariations") or []:  # VERIFY
            pkg_raw = (var.get("PackageType") or {}).get("Name")  # VERIFY
            offers.append(DistributorOffer(
                distributor_id=self.distributor_id,
                distributor_name=self.display_name,
                sku=var.get("DigiKeyProductNumber"),
                source=OfferSource.DIRECT_API,
                authorized_seller=True,
                stock_qty=max(int(var.get("QuantityAvailableforPackageType") or 0), 0),
                moq=var.get("MinimumOrderQuantity") or None,
                order_multiple=var.get("StandardPackage") or None,  # VERIFY semantics
                packaging=map_packaging(pkg_raw),
                packaging_raw=pkg_raw,
                price_breaks=[
                    PriceBreak(
                        quantity=pb["BreakQuantity"],
                        unit_price=Decimal(str(pb["UnitPrice"])),
                        currency="USD",
                    )
                    for pb in var.get("StandardPricing") or []
                ],
                lead_time_days=lead_days,
                lead_time_semantics=(
                    LeadTimeSemantics.FACTORY if lead_days is not None
                    else LeadTimeSemantics.UNSPECIFIED
                ),
                lead_time_raw=(
                    f"{product.get('ManufacturerLeadWeeks')} weeks"
                    if product.get("ManufacturerLeadWeeks") is not None else None
                ),
                buy_url=product_url,
            ))
        return offers
