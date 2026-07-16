# src/cie/compliance/models.py
"""Output contract for M4 — same shape family as M2/M3 so the report's
dimensions read consistently. Reuses ScoreReason and the band mapping."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from cie.lifecycle.models import RiskBand, ScoreReason, band_for  # noqa: F401


class ComplianceScore(BaseModel):
    """M4's verdict on one part's regulatory posture."""
    model_config = ConfigDict(frozen=True)

    rubric_version: str = "1.0"
    mpn: str
    score: int = Field(ge=0, le=100)
    band: RiskBand
    reasons: list[ScoreReason]
    unknowns: list[str]
    source: str | None = None      # which data source stated the raw facts
    scored_at: datetime
