# src/cie/ingestion/pipeline.py
"""M1 orchestrator: resolve via Nexar -> enrich via enabled direct APIs ->
dedup offers (direct-API wins) -> classify -> fetch datasheet -> PartRecord.

Depends only on the DistributorClient ABC — adapters are injected, and a
failed or disabled adapter degrades the record, never the run."""
import logging
from datetime import datetime, timezone

import httpx

from cie.cache.store import CacheStore
from cie.config import Settings, get_settings
from cie.ingestion.classify import classify
from cie.ingestion.clients.arrow import ArrowClient
from cie.ingestion.clients.avnet import AvnetClient
from cie.ingestion.clients.base import DistributorClient
from cie.ingestion.clients.digikey import DigiKeyClient
from cie.ingestion.clients.mouser import MouserClient
from cie.ingestion.clients.oemsecrets import OemSecretsClient
from cie.ingestion.datasheets import fetch_datasheet
from cie.ingestion.mpn import choose_candidate, normalize
from cie.models.enums import LifecycleStatus, OfferSource
from cie.models.part import (ComplianceInfo, DistributorOffer, IngestionMeta,
                             PartRecord)

logger = logging.getLogger(__name__)

# Direct-API distributor ids, used to drop Nexar-relayed duplicates.
_DIRECT_IDS = {"digikey", "digi-key", "digi_key", "mouser", "arrow",
               "avnet", "avnet_americas", "avnet_europe"}
_CANONICAL = {"digi-key": "digikey", "digi_key": "digikey",
              "avnet_americas": "avnet", "avnet_europe": "avnet"}


def _map_lifecycle(raw: str | None) -> LifecycleStatus:
    """Map a spine's raw lifecycle string (e.g. Digi-Key ProductStatus) to
    the enum. Unrecognized strings stay UNKNOWN with the raw preserved."""
    if not raw:
        return LifecycleStatus.UNKNOWN
    s = raw.strip().lower()
    # Calibration decision (approved 2026-07): distributor-scoped
    # discontinuation ("Discontinued at DigiKey") is a death rattle but
    # not the manufacturer's own verdict -> EOL (35), one notch below
    # manufacturer-official Obsolete (40). Raw string always preserved.
    if "discontinued at" in s:
        return LifecycleStatus.EOL
    if "obsolete" in s or "discontinued" in s:
        return LifecycleStatus.OBSOLETE
    if "not recommended" in s or "nrnd" in s:
        return LifecycleStatus.NRND
    if "last time buy" in s or "end of life" in s or "eol" in s:
        return LifecycleStatus.EOL
    if "active" in s or "in production" in s:
        return LifecycleStatus.ACTIVE
    if "new" in s or "preliminary" in s:
        return LifecycleStatus.NEW
    return LifecycleStatus.UNKNOWN


class MpnNotFoundError(Exception):
    """The active spine returned no candidates for the queried MPN."""


class IngestionPipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.cache = CacheStore(self.settings)
        self.direct_clients: list[DistributorClient] = [
            cls(self.settings, self.cache)
            for cls in (DigiKeyClient, MouserClient, ArrowClient, AvnetClient)
        ]
        self.oemsecrets = OemSecretsClient(self.settings, self.cache)
        # Spine priority (Nexar erased from the architecture, 2026-07):
        #   1. OEMsecrets — free aggregator, market-wide view
        #   2. Digi-Key   — free direct API doubling as resolver
        self._digikey = next(c for c in self.direct_clients
                             if c.distributor_id == "digikey")
        if self.oemsecrets.is_enabled:
            self.spine, self.spine_name = self.oemsecrets, "oemsecrets"
        else:
            self.spine, self.spine_name = self._digikey, "digikey"

    def run(self, query_mpn: str) -> PartRecord:
        """Ingest one MPN into a canonical PartRecord."""
        if not self.spine.is_enabled:
            raise RuntimeError("No spine available — configure an OEMsecrets "
                               "key (preferred) or Digi-Key keys.")
        mpn = normalize(query_mpn)
        sources_queried = [self.spine_name]
        _strip = lambda s: __import__("re").sub(r"[^A-Z0-9]", "", s.upper())
        sources_failed, cache_hits = [], []
        fetch_times: list[datetime] = []

        # 1. Resolve + aggregated offers via the active spine.
        candidates, spine_fetched = self.spine.search(mpn)
        if not candidates:
            raise MpnNotFoundError(
                f"No {self.spine_name} match for '{query_mpn}'")
        chosen, alternates = choose_candidate(mpn, candidates)
        assert chosen is not None

        offers: list[DistributorOffer] = list(chosen.offers)
        if offers:
            fetch_times.append(spine_fetched)

        # 2. Enrich with every ENABLED direct API; failures degrade, not abort.
        # Aggregator spines may return punctuation-squashed part numbers
        # (e.g. 'NX3225SA24000MHZSTDCSR1'); when the resolved part equals the
        # query modulo formatting, direct stores get the human's formatting.
        enrich_mpn = (query_mpn if _strip(chosen.mpn) == _strip(query_mpn)
                      else chosen.mpn)
        for client in self.direct_clients:
            if not client.is_enabled:
                continue
            if client is self.spine:
                continue  # spine already contributed its offers above
            sources_queried.append(client.distributor_id)
            try:
                direct, fetched_at, hit = client.get_offers(enrich_mpn)
            except Exception as exc:  # noqa: BLE001 — ANY adapter failure
                # (network, API change, or even a parsing bug) must degrade
                # this source, never abort the part. Design guarantee.
                logger.warning("%s failed for %s: %s",
                               client.distributor_id, chosen.mpn, exc)
                sources_failed.append(client.distributor_id)
                continue
            if hit:
                cache_hits.append(client.distributor_id)
            if direct:
                fetch_times.append(fetched_at)
                offers.extend(direct)

        # 3. Dedup: direct-API beats Nexar-relayed for the same distributor.
        offers = self._dedup(offers)

        # 4. Classify.
        category, cat_source = classify(
            chosen.category_path, chosen.category_name, chosen.description)

        # 5. Datasheet (best-effort).
        datasheet = fetch_datasheet(chosen.datasheet_url, chosen.mpn, self.settings)

        comp_dict = dict(chosen.compliance or {})
        comp_source = self.spine_name if comp_dict else None
        _missing = [k for k in ("rohs", "reach", "eccn", "msl")
                    if k not in comp_dict]
        if _missing and self.spine is not self._digikey \
                and self._digikey.is_enabled:
            # COMPLIANCE SPECIALIST fallback: the aggregator spine carries
            # no compliance data; ask Digi-Key's details endpoint for just
            # the Classifications block (cached 7 days, one call per part).
            try:
                fetched = self._digikey.get_compliance(enrich_mpn)
                added = False
                for k in _missing:
                    if k in fetched:
                        comp_dict[k] = fetched[k]
                        added = True
                if added:
                    comp_source = (f"{comp_source}+digikey"
                                   if comp_source else "digikey")
            except Exception as exc:
                logger.warning("compliance harvest via digikey failed: %s",
                               exc)
        comp_info = (ComplianceInfo(
            rohs_raw=comp_dict.get("rohs"),
            reach_raw=comp_dict.get("reach"),
            eccn_raw=comp_dict.get("eccn"),
            msl_raw=comp_dict.get("msl"),
            source=comp_source,
        ) if comp_dict else None)

        return PartRecord(
            query_mpn=query_mpn,
            mpn=chosen.mpn,
            manufacturer=chosen.manufacturer,
            description=chosen.description,
            spine_part_id=chosen.source_part_id,
            alternates_considered=alternates,
            category=category,
            category_source=cat_source,
            compliance=comp_info,
            lifecycle_status=_map_lifecycle(chosen.lifecycle_status_raw),
            lifecycle_status_raw=chosen.lifecycle_status_raw,
            factory_lead_days=chosen.factory_lead_days,
            parametrics=chosen.parametrics,
            offers=offers,
            availability_snapshot_ts=min(fetch_times) if fetch_times else None,
            datasheet=datasheet,
            meta=IngestionMeta(
                sources_queried=sources_queried,
                sources_failed=sources_failed,
                cache_hits=cache_hits,
                ingested_at=datetime.now(timezone.utc),
            ),
        )

    @staticmethod
    def _dedup(offers: list[DistributorOffer]) -> list[DistributorOffer]:
        """Key (distributor_id, sku) falling back to (distributor_id,
        packaging). Direct-API entries win; a distributor with ANY direct
        offers has all its Nexar-relayed rows dropped (partial relays of the
        same position under a different SKU string are the common case)."""
        def canon(d: str) -> str:
            return _CANONICAL.get(d, d)

        direct_dists = {canon(o.distributor_id) for o in offers
                        if o.source == OfferSource.DIRECT_API}
        kept: dict[tuple[str, str], DistributorOffer] = {}
        for o in offers:
            cid = canon(o.distributor_id)
            if (o.source != OfferSource.DIRECT_API
                    and cid in _DIRECT_IDS and cid in direct_dists):
                continue
            key = (cid, o.sku or f"pkg:{o.packaging.value}")
            prev = kept.get(key)
            if prev is None or (prev.source != OfferSource.DIRECT_API
                                and o.source == OfferSource.DIRECT_API):
                kept[key] = o
        return list(kept.values())
