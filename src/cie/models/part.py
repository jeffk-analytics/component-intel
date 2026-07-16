# src/cie/models/part.py
"""Canonical part record — the contract between M1 and every downstream module.

Frozen at end of Phase 1. Design rules baked in:
  * Availability is a flat list of per-distributor offers; zero stock is not
    a special case (stock_qty=0 with lead-time fields populated or null).
  * Lead times are never invented. days=None means "no stated lead time" and
    downstream modules must treat that as a risk signal, not missing data.
  * Availability freshness is RECORD-level (decision #6): the oldest fetch
    time among the sources that contributed offers — a guarantee that no
    offer in the record is staler than that timestamp.
"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .enums import (
    LeadTimeSemantics,
    LifecycleStatus,
    OfferSource,
    Packaging,
    PilotCategory,
)


class PriceBreak(BaseModel):
    """One row of a distributor's quantity/price ladder."""
    model_config = ConfigDict(frozen=True)

    quantity: int = Field(ge=1)            # break quantity (1, 10, 100, ...)
    unit_price: Decimal                    # Decimal, never float, for money
    currency: str = "USD"                  # ISO 4217


class IncomingStock(BaseModel):
    """One inbound-stock line item, if the API exposes it."""
    model_config = ConfigDict(frozen=True)

    quantity: int = Field(ge=0)
    expected_date: date | None = None      # None = quantity known, date not stated


class DistributorOffer(BaseModel):
    """A single distributor's availability position for the resolved part.

    Dedup key is (distributor_id, sku) when sku is present, else
    (distributor_id, packaging). Same distributor legitimately appears
    multiple times with different packaging (cut tape vs full reel).
    """
    model_config = ConfigDict(frozen=True)

    distributor_id: str                    # canonical lowercase id: "digikey",
                                           # "mouser", "arrow", "avnet", or a
                                           # normalized Nexar seller slug
    distributor_name: str                  # display name as reported upstream
    sku: str | None = None                 # distributor's own part number
    source: OfferSource                    # direct API vs Nexar-relayed
    authorized_seller: bool | None = None  # Nexar flags brokers; direct APIs
                                           # are authorized by definition (True)

    stock_qty: int = Field(ge=0)           # 0 is a first-class value, not missing
    moq: int | None = Field(default=None, ge=1)   # minimum order quantity
    order_multiple: int | None = None      # order-in-multiples-of
    packaging: Packaging = Packaging.UNKNOWN
    packaging_raw: str | None = None       # upstream string, kept for audit
    price_breaks: list[PriceBreak] = Field(default_factory=list)

    # --- lead time: the "never invent" rule lives here -------------------
    lead_time_days: int | None = Field(default=None, ge=0)
    #   None  = no stated lead time (report as such — it's a risk signal)
    #   int   = calendar days, normalized per the leadtime module
    lead_time_semantics: LeadTimeSemantics = LeadTimeSemantics.UNSPECIFIED
    lead_time_raw: str | None = None       # verbatim upstream value ("12 wks")

    incoming: list[IncomingStock] = Field(default_factory=list)

    buy_url: HttpUrl | None = None         # direct buy-now link when available


class ComplianceInfo(BaseModel):
    """Raw compliance classifications as stated by the data source
    (schema 1.3). Raw strings preserved verbatim — normalization and
    judgment happen in M4's scorer, never at ingestion."""
    model_config = ConfigDict(frozen=True)

    rohs_raw: str | None = None     # e.g. "RoHS Compliant"
    reach_raw: str | None = None    # e.g. "REACH Unaffected"
    eccn_raw: str | None = None     # e.g. "EAR99"
    msl_raw: str | None = None      # e.g. "1 (Unlimited)"
    source: str | None = None       # which source stated these


class DatasheetRef(BaseModel):
    """Pointer to the retrieved manufacturer datasheet."""
    model_config = ConfigDict(frozen=True)

    url: HttpUrl                           # source URL (usually via Nexar)
    local_path: str | None = None          # path under data/datasheets/ once
                                           # downloaded; None if fetch failed
    retrieved_at: datetime | None = None


class AlternateMatch(BaseModel):
    """A candidate the disambiguator considered but did not select."""
    model_config = ConfigDict(frozen=True)

    mpn: str
    manufacturer: str
    note: str                              # human-readable reason, e.g.
                                           # "packaging-variant suffix -TR"


class IngestionMeta(BaseModel):
    """Operational metadata — feeds the harness metrics directly."""
    model_config = ConfigDict(frozen=True)

    sources_queried: list[str] = Field(default_factory=list)  # adapters run
    sources_failed: list[str] = Field(default_factory=list)   # errored/timeout
    cache_hits: list[str] = Field(default_factory=list)       # served from cache
    ingested_at: datetime                  # UTC; end of pipeline run


class PartRecord(BaseModel):
    """The canonical output of M1 and sole input contract for M2-M6."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.3"            # 1.3: compliance block added (M4)

    # --- identity ---------------------------------------------------------
    query_mpn: str                         # exactly what the user typed
    mpn: str                               # resolved canonical MPN
    manufacturer: str                      # as reported by Nexar
    description: str | None = None
    spine_part_id: str | None = None       # the active spine's stable handle
                                           # (renamed from nexar_part_id in 1.2
                                           # when Nexar was erased) so M2/M5 can
                                           # re-query without re-resolving
    alternates_considered: list[AlternateMatch] = Field(default_factory=list)

    # --- classification ----------------------------------------------------
    category: PilotCategory = PilotCategory.UNCLASSIFIED
    category_source: str | None = None     # e.g. "nexar_taxonomy:Ceramic Capacitors"

    # --- lifecycle (raw signal only; scoring is M2's job) ------------------
    lifecycle_status: LifecycleStatus = LifecycleStatus.UNKNOWN
    lifecycle_status_raw: str | None = None
    factory_lead_days: int | None = None   # part-level factory lead per Nexar,
                                           # distinct from per-offer lead times

    # --- parametrics (raw; typed normalization deferred to M5) -------------
    parametrics: dict[str, str] = Field(default_factory=dict)

    # --- availability ------------------------------------------------------
    offers: list[DistributorOffer] = Field(default_factory=list)
    # Empty list = part resolved but no distributor coverage found anywhere.
    # All-zero stock_qty = the "sourcing desert" case M2 cares about.
    availability_snapshot_ts: datetime | None = None
    #   UTC. Oldest fetch time among the sources that contributed offers —
    #   i.e. a guarantee: "no offer above is staler than this". None only
    #   when offers is empty.

    # --- documents ---------------------------------------------------------
    datasheet: DatasheetRef | None = None
    compliance: ComplianceInfo | None = None   # added in schema 1.3 (M4)

    # --- provenance --------------------------------------------------------
    meta: IngestionMeta
