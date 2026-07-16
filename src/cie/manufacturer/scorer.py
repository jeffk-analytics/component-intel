# src/cie/manufacturer/scorer.py
"""M3 scorer: who stands behind the part, and how nervous should that
make you? Same discipline as M2 — every point itemized, unknowns cost
modest points and are named, no network calls.

Rubric v1.0 (0-100, higher = riskier):
  brand lineage      up to 35   part's original brand was absorbed by an
                                acquirer; risk decays with years since
                                (fresh integrations prune hardest)
  corporate churn    up to 15   the owner is a serial acquirer (last 10y)
  observed pruning   up to 30   fraction of THIS maker's parts that WE have
                                scanned and found dead/dying; scores only
                                once the sample reaches 3+ parts, always
                                disclosed either way
  identity           up to 15   manufacturer not in the reference file at
                                all -> modest unknown penalty
  renames            0          informational only (onsemi, Nexperia)
"""
from datetime import datetime, timezone

from cie.manufacturer.knowledge import lookup
from cie.manufacturer.models import ManufacturerScore, ScoreReason, band_for
from cie.models.part import PartRecord

_MIN_SAMPLE = 3   # observed-pruning scores only at n >= 3


def score_manufacturer(
    record: PartRecord,
    observed_sample: int = 0,
    observed_dead_fraction: float = 0.0,
) -> ManufacturerScore:
    info = lookup(record.manufacturer, record.mpn)
    reasons: list[ScoreReason] = []
    unknowns: list[str] = []
    total = 0
    this_year = datetime.now(timezone.utc).year

    # ---- identity ----------------------------------------------------------
    if info["entry"] is None:
        total += 15
        unknowns.append("manufacturer_identity")
        reasons.append(ScoreReason(
            signal="identity", points=15,
            detail=f"'{record.manufacturer}' not in the curated reference "
                   f"file — unvetted maker (extend knowledge.json to fix)"))
    else:
        note = info["entry"].get("stability_note", "")
        reasons.append(ScoreReason(
            signal="identity", points=0,
            detail=f"recognized as {info['canonical']}"
                   + (f" — {note}" if note else "")))

    # ---- brand lineage -----------------------------------------------------
    ab = info["absorbed"]
    if ab:
        age = this_year - ab["year"]
        if age < 3:
            pts = 35
        elif age < 7:
            pts = 25
        elif age < 12:
            pts = 12
        else:
            pts = 6
        total += pts
        reasons.append(ScoreReason(
            signal="brand_lineage", points=pts,
            detail=f"{ab['brand']} lineage ({ab['evidence']}), absorbed by "
                   f"{ab['owner']} in {ab['year']} ({age} years ago) — "
                   f"absorbed portfolios face pruning"))
    else:
        reasons.append(ScoreReason(
            signal="brand_lineage", points=0,
            detail="no absorbed-brand lineage detected"))

    # ---- corporate churn ---------------------------------------------------
    entry = info["entry"]
    recent = [b for b in (entry or {}).get("absorbed_brands", [])
              if this_year - b["year"] <= 10]
    if len(recent) >= 3:
        pts = 15
    elif len(recent) == 2:
        pts = 8
    elif len(recent) == 1:
        pts = 4
    else:
        pts = 0
    total += pts
    names = ", ".join(f"{b['brand']} {b['year']}" for b in recent)
    reasons.append(ScoreReason(
        signal="corporate_churn", points=pts,
        detail=(f"owner absorbed {len(recent)} brand(s) in the last decade "
                f"({names}) — integration churn" if recent
                else "no major acquisitions by the owner in the last decade")))

    # ---- observed pruning (our own scan ledger) ----------------------------
    if observed_sample >= _MIN_SAMPLE:
        pts = round(observed_dead_fraction * 30)
        total += pts
        reasons.append(ScoreReason(
            signal="observed_pruning", points=pts,
            detail=f"{observed_dead_fraction:.0%} of the {observed_sample} "
                   f"parts we have scanned from this maker are dead/dying "
                   f"(small sample — grows as the ledger grows)"))
    else:
        reasons.append(ScoreReason(
            signal="observed_pruning", points=0,
            detail=f"insufficient sample to score ({observed_sample} part(s) "
                   f"scanned; scoring activates at {_MIN_SAMPLE}) — "
                   f"informational only"))

    # ---- renames (informational) -------------------------------------------
    for rn in info["renames"]:
        reasons.append(ScoreReason(
            signal="rename", points=0,
            detail=f"note: {rn['from']} became {rn['to']} in {rn['year']} "
                   f"(same company; older documents use the old name)"))

    score = min(total, 100)
    return ManufacturerScore(
        manufacturer=record.manufacturer,
        canonical=info["canonical"],
        score=score,
        band=band_for(score),
        reasons=reasons,
        unknowns=unknowns,
        observed_sample=observed_sample,
        scored_at=datetime.now(timezone.utc),
    )
