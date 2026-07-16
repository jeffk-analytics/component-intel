# src/cie/models/enums.py
"""Controlled vocabularies for the canonical part record.

These enums are part of the frozen Phase-1 schema. Every enum that maps
messy upstream API strings includes an UNKNOWN member so ingestion never
fails on a value we haven't seen — the raw string is preserved alongside
it in the owning model for audit.
"""
from enum import Enum


class OfferSource(str, Enum):
    """Provenance of an availability offer. Direct-API data wins dedup ties."""
    DIRECT_API = "direct_api"              # Digi-Key / Mouser / Arrow / Avnet API
    NEXAR_AGGREGATED = "nexar_aggregated"  # offer relayed via Nexar/Octopart
    OEMSECRETS_AGGREGATED = "oemsecrets_aggregated"  # relayed via OEMsecrets
    # (added in schema 1.1 with the approved spine switch, 2026-07)


class Packaging(str, Enum):
    """Order packaging. Upstream strings vary wildly; UNKNOWN + raw string
    covers anything unmapped."""
    CUT_TAPE = "cut_tape"
    REEL = "reel"
    TAPE_AND_REEL = "tape_and_reel"    # some APIs don't distinguish reel sizes
    TRAY = "tray"
    TUBE = "tube"
    BULK = "bulk"
    BAG = "bag"
    UNKNOWN = "unknown"


class LeadTimeSemantics(str, Enum):
    """What kind of lead time a source actually quoted. Never inferred —
    UNSPECIFIED means the API gave a number without saying which it is."""
    FACTORY = "factory"                # manufacturer lead time
    DISTRIBUTOR = "distributor"        # distributor's own quoted lead time
    UNSPECIFIED = "unspecified"


class LifecycleStatus(str, Enum):
    """Coarse lifecycle signal as reported by Nexar. M2 will build its own
    richer score; this is just the raw upstream classification."""
    ACTIVE = "active"
    NRND = "nrnd"                      # not recommended for new designs
    EOL = "eol"                        # end-of-life announced
    OBSOLETE = "obsolete"
    NEW = "new"                        # pre-release / recently introduced
    UNKNOWN = "unknown"


class PilotCategory(str, Enum):
    """The 5 Phase-1 categories plus an explicit escape hatch."""
    MLCC = "mlcc"
    BUCK_CONVERTER = "buck_converter"
    MICROCONTROLLER = "microcontroller"
    CRYSTAL_OSCILLATOR = "crystal_oscillator"
    CAN_TRANSCEIVER = "can_transceiver"
    UNCLASSIFIED = "unclassified"      # resolved fine, just not a pilot category
