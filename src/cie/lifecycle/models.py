# src/cie/lifecycle/models.py
"""Output contract for M2: a lifecycle health score that shows its work.

Design rules (approved 2026-07):
  * 0-100 score, higher = riskier, plus a named band.
  * Every point is itemized in `reasons` — the score is auditable.
  * Unknown signals add modest risk (the lead-time rule, generalized)
    and are listed in `unknowns` so gaps are visible, never silent.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RiskBand(str, Enum):
    HEALTHY = "healthy"      # 0-24
    WATCH = "watch"          # 25-49
    AT_RISK = "at_risk"      # 50-74
    CRITICAL = "critical"    # 75-100


class ScoreReason(BaseModel):
    """One line of the itemized bill: which signal, how many points, why."""
    model_config = ConfigDict(frozen=True)

    signal: str          # "lifecycle_status" | "availability" | "lead_time"
                         # | "datasheet_age" | "trend"
    points: int          # contribution to the score (0 is shown, not hidden)
    detail: str          # human-readable justification


class LifecycleScore(BaseModel):
    """M2's verdict for one part."""
    model_config = ConfigDict(frozen=True)

    rubric_version: str = "1.1"    # bump whenever weights/thresholds change
    mpn: str
    score: int = Field(ge=0, le=100)
    band: RiskBand
    reasons: list[ScoreReason]
    unknowns: list[str]            # signals we could not observe
    history_snapshots: int = 0     # how much trend history exists so far
    scored_at: datetime


def band_for(score: int) -> RiskBand:
    if score >= 75:
        return RiskBand.CRITICAL
    if score >= 50:
        return RiskBand.AT_RISK
    if score >= 25:
        return RiskBand.WATCH
    return RiskBand.HEALTHY
