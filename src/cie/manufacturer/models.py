# src/cie/manufacturer/models.py
"""Output contract for M3. Deliberately mirrors M2's LifecycleScore —
same 0-100 scale, same bands, same itemized-reasons discipline — so the
final report's dimensions read consistently. ScoreReason and the band
mapping are REUSED from the lifecycle module rather than duplicated."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from cie.lifecycle.models import RiskBand, ScoreReason, band_for  # noqa: F401


class ManufacturerScore(BaseModel):
    """M3's verdict on the company behind one part."""
    model_config = ConfigDict(frozen=True)

    rubric_version: str = "1.0"
    manufacturer: str              # as reported by the data sources
    canonical: str | None          # our reference file's canonical name
    score: int = Field(ge=0, le=100)
    band: RiskBand
    reasons: list[ScoreReason]
    unknowns: list[str]
    observed_sample: int = 0       # how many of this maker's parts WE have
                                   # scanned (small-sample disclosure)
    scored_at: datetime
