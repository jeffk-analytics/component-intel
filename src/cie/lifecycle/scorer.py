# src/cie/lifecycle/scorer.py
"""M2 scorer: PartRecord in, auditable LifecycleScore out.

Approved rubric v1.1 (weights subject to tuning against real parts):
  lifecycle status   up to 40    manufacturer's own verdict
  availability now   up to 30    breadth + depth of stock today
  lead time          up to 20    long or UNSTATED waits (the founding rule)
  datasheet age      up to 10    stale documentation as a proxy signal
  trend              0 for now   informational until the history log matures
Unknown signals add modest points and are listed in `unknowns` — a gap is
a risk, never a blank. This module performs NO network calls.
"""
from datetime import datetime, timezone

from cie.lifecycle.datasheet_age import extract_datasheet_year
from cie.lifecycle.models import LifecycleScore, ScoreReason, band_for
from cie.models.enums import LifecycleStatus, PilotCategory
from cie.models.part import PartRecord

# "Thin stock" thresholds per pilot category — deliberately rough v1
# heuristics (a healthy jellybean capacitor market holds far more units
# than a healthy MCU market). Tunable without touching the scorer logic.
_THIN_STOCK: dict[PilotCategory, int] = {
    PilotCategory.MLCC: 10000,
    PilotCategory.BUCK_CONVERTER: 2000,
    PilotCategory.MICROCONTROLLER: 1000,
    PilotCategory.CRYSTAL_OSCILLATOR: 5000,
    PilotCategory.CAN_TRANSCEIVER: 2000,
    PilotCategory.UNCLASSIFIED: 1000,
}

_STATUS_POINTS: dict[LifecycleStatus, tuple[int, str]] = {
    LifecycleStatus.ACTIVE: (0, "manufacturer reports Active"),
    LifecycleStatus.NEW: (0, "manufacturer reports New/Preliminary"),
    LifecycleStatus.UNKNOWN: (10, "no lifecycle status stated by any source"),
    LifecycleStatus.NRND: (30, "Not Recommended for New Designs"),
    LifecycleStatus.EOL: (35, "End of Life announced"),
    LifecycleStatus.OBSOLETE: (40, "manufacturer reports Obsolete"),
}


def score_lifecycle(
    record: PartRecord, history_snapshots: int = 0
) -> LifecycleScore:
    """Apply rubric v1.1 to one canonical part record."""
    reasons: list[ScoreReason] = []
    unknowns: list[str] = []

    # ---- signal 1: manufacturer lifecycle status (max 40) ----------------
    pts, detail = _STATUS_POINTS[record.lifecycle_status]
    if record.lifecycle_status_raw:
        # never overstate who said it: quote the raw evidence verbatim
        detail = f"{detail} (source says: '{record.lifecycle_status_raw}')"
    if record.lifecycle_status == LifecycleStatus.UNKNOWN:
        unknowns.append("lifecycle_status")
    reasons.append(ScoreReason(signal="lifecycle_status", points=pts,
                               detail=detail))
    total = pts

    # ---- signal 2: market availability today (max 30) --------------------
    pts, detail = _availability_points(record)
    reasons.append(ScoreReason(signal="availability", points=pts,
                               detail=detail))
    total += pts

    # ---- signal 3: lead time (max 20) -------------------------------------
    pts, detail, unknown = _lead_time_points(record)
    if unknown:
        unknowns.append("lead_time")
    reasons.append(ScoreReason(signal="lead_time", points=pts, detail=detail))
    total += pts

    # ---- signal 4: datasheet age (max 10) ---------------------------------
    year = extract_datasheet_year(
        record.datasheet.local_path if record.datasheet else None)
    pts, detail = _datasheet_points(year)
    if year is None:
        unknowns.append("datasheet_age")
    reasons.append(ScoreReason(signal="datasheet_age", points=pts,
                               detail=detail))
    total += pts

    # ---- signal 5: trend (informational until history matures) ------------
    reasons.append(ScoreReason(
        signal="trend", points=0,
        detail=(f"insufficient history ({history_snapshots} snapshot(s) on "
                f"file); trend scoring activates after 3+ snapshots spanning "
                f"2+ weeks")))

    score = min(total, 100)
    return LifecycleScore(
        mpn=record.mpn,
        score=score,
        band=band_for(score),
        reasons=reasons,
        unknowns=unknowns,
        history_snapshots=history_snapshots,
        scored_at=datetime.now(timezone.utc),
    )


def _availability_points(record: PartRecord) -> tuple[int, str]:
    if not record.offers:
        return 30, "no distributor coverage found at any configured source"
    total_stock = sum(o.stock_qty for o in record.offers)
    if total_stock == 0:
        return 25, "zero stock at every configured source"
    stocked = {o.distributor_id for o in record.offers if o.stock_qty > 0}
    # Breadth is judged RELATIVE to sources we could actually observe
    # (rubric 1.1 fix: with only two sources configured, stocking at both
    # is full coverage, not "narrow sourcing" — parts must not be
    # penalized for the limits of OUR telescope).
    available = max(1, len(set(record.meta.sources_queried))
                    - len(set(record.meta.sources_failed)))
    target = min(3, available)
    pts = 0
    parts = [f"{total_stock:,} units across {len(stocked)} of "
             f"{available} observable distributor(s)"]
    if len(stocked) >= target:
        if available < 3:
            parts.append("full coverage of configured sources (breadth "
                         "re-assessed when more sources come online)")
    elif len(stocked) == 2:
        pts += 6
        parts.append("narrow sourcing: two distributors stocking")
    elif len(stocked) == 1:
        pts += 12
        parts.append("single-source: only one distributor stocking "
                     "despite wider observable coverage")
    threshold = _THIN_STOCK.get(record.category, 1000)
    if total_stock < threshold:
        pts += 8
        parts.append(f"thin stock for a {record.category.value} "
                     f"(heuristic threshold {threshold:,})")
    return min(pts, 30), "; ".join(parts)


def _lead_time_points(record: PartRecord) -> tuple[int, str, bool]:
    """Returns (points, detail, lead_time_unknown)."""
    leads = [o.lead_time_days for o in record.offers
             if o.lead_time_days is not None]
    if record.factory_lead_days is not None:
        leads.append(record.factory_lead_days)
    total_stock = sum(o.stock_qty for o in record.offers)
    if not leads:
        if total_stock == 0:
            return 12, ("no source states a lead time AND the part is out "
                        "of stock — an unknown wait is itself a risk"), True
        return 4, "no stated lead time (in stock, so mild)", True
    best = min(leads)
    if best <= 56:
        return 0, f"best stated lead time {best} days (≤8 weeks)", False
    if best <= 112:
        return 8, f"best stated lead time {best} days (8-16 weeks)", False
    if best <= 182:
        return 14, f"best stated lead time {best} days (16-26 weeks)", False
    return 18, f"best stated lead time {best} days (>26 weeks)", False


def _datasheet_points(year: int | None) -> tuple[int, str]:
    if year is None:
        return 3, "datasheet revision year could not be determined"
    age = datetime.now(timezone.utc).year - year
    if age < 5:
        return 0, f"datasheet revision year {year} (current)"
    if age < 10:
        return 4, f"datasheet revision year {year} ({age} years old)"
    if age < 15:
        return 7, f"datasheet revision year {year} ({age} years old)"
    return 10, f"datasheet revision year {year} ({age}+ years old)"
