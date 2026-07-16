# src/cie/substitution/models.py
"""Output contract for M5. A substitution report is the engine's final
form: candidates gathered, fitness judged with evidence, health scored
by the M2/M3/M4 machinery, ranked with the whole bill visible."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from cie.substitution.fit import FitVerdict


class SubstituteCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    mpn: str
    manufacturer: str
    source: str                    # "px4_alternates" | "digikey_discovery"
    fit: FitVerdict
    fit_evidence: list[str]
    fit_unknowns: list[str]
    lifecycle_score: int | None = None
    manufacturer_score: int | None = None
    compliance_score: int | None = None
    composite: float | None = None   # 0.5*life + 0.25*mfr + 0.25*cmp
    total_stock: int | None = None
    error: str | None = None       # candidate lookups may fail; reported, never hidden


class SubstitutionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_mpn: str
    reference_category: str
    generated_at: datetime
    policy_block: str | None = None   # set when the category forbids auto-sub
    ranked: list[SubstituteCandidate]        # FIT first, by composite asc
    unverified: list[SubstituteCandidate]    # nothing violated, gaps remain
    rejected: list[SubstituteCandidate]      # failed a sacred parameter
