# src/cie/ingestion/clients/base.py
"""The distributor adapter contract. Core M1 logic depends ONLY on this
module — adding or disabling a distributor never touches the pipeline."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from cie.cache.store import CacheStore
from cie.config import Settings
from cie.models.part import DistributorOffer


class DistributorClient(ABC):
    """One adapter per direct distributor API.

    Subclasses implement `_fetch_offers` (live network call). The public
    `get_offers` wraps it with the short-TTL availability cache and returns
    the fetch timestamp so the pipeline can compute record-level freshness.
    """

    distributor_id: ClassVar[str]      # canonical lowercase id, e.g. "digikey"
    display_name: ClassVar[str]

    def __init__(self, settings: Settings, cache: CacheStore) -> None:
        self.settings = settings
        self.cache = cache

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """True iff this adapter's credentials are configured."""

    @abstractmethod
    def _fetch_offers(self, mpn: str) -> list[DistributorOffer]:
        """Live API call. May raise httpx.HTTPError; pipeline handles it."""

    def get_offers(self, mpn: str) -> tuple[list[DistributorOffer], datetime, bool]:
        """Cached offer lookup.

        Returns (offers, fetched_at_utc, cache_hit). fetched_at is when the
        data actually left the API, not when this call ran.
        """
        key = f"offers:{self.distributor_id}:{mpn.upper()}"
        cached = self.cache.get_availability(key)
        if cached is not None:
            payload, fetched_at = cached
            offers = [DistributorOffer.model_validate_json(o) for o in payload]
            return offers, fetched_at, True
        offers = self._fetch_offers(mpn)
        fetched_at = self.cache.set_availability(
            key, [o.model_dump_json() for o in offers]
        )
        return offers, fetched_at, False
