# src/cie/compliance/scorer.py
"""M4 scorer: regulatory and manufacturability posture of one part.
Same discipline as M2/M3: 0-100 (higher = riskier), every point itemized,
unknowns cost modest points and are named. No network calls — judges the
compliance block M1 now carries on the record.

Rubric v1.0:
  RoHS               up to 40   non-compliant parts are unsellable into EU
                                products; the headline signal
  REACH              up to 25   SVHC content triggers declaration duty
  export control     up to 20   non-EAR99 classifications restrict who a
                                finished product may ship to
  moisture (MSL)     up to 15   factory-floor handling burden; minor
Raw strings are normalized here, never at ingestion — the record keeps
what the source actually said.
"""
from datetime import datetime, timezone

from cie.lifecycle.models import ScoreReason, band_for
from cie.compliance.models import ComplianceScore
from cie.models.part import PartRecord


def score_compliance(record: PartRecord) -> ComplianceScore:
    c = record.compliance
    reasons: list[ScoreReason] = []
    unknowns: list[str] = []
    total = 0

    # ---- RoHS (max 40) -----------------------------------------------------
    pts, detail, unknown = _rohs(c.rohs_raw if c else None)
    total += pts
    if unknown:
        unknowns.append("rohs")
    reasons.append(ScoreReason(signal="rohs", points=pts, detail=detail))

    # ---- REACH (max 25) ----------------------------------------------------
    pts, detail, unknown = _reach(c.reach_raw if c else None)
    total += pts
    if unknown:
        unknowns.append("reach")
    reasons.append(ScoreReason(signal="reach", points=pts, detail=detail))

    # ---- Export control (max 20) -------------------------------------------
    pts, detail, unknown = _eccn(c.eccn_raw if c else None)
    total += pts
    if unknown:
        unknowns.append("export_control")
    reasons.append(ScoreReason(signal="export_control", points=pts,
                               detail=detail))

    # ---- Moisture sensitivity (max 15) --------------------------------------
    pts, detail, unknown = _msl(c.msl_raw if c else None)
    total += pts
    if unknown:
        unknowns.append("moisture_sensitivity")
    reasons.append(ScoreReason(signal="moisture_sensitivity", points=pts,
                               detail=detail))

    score = min(total, 100)
    return ComplianceScore(
        mpn=record.mpn,
        score=score,
        band=band_for(score),
        reasons=reasons,
        unknowns=unknowns,
        source=(c.source if c else None),
        scored_at=datetime.now(timezone.utc),
    )


def _rohs(raw: str | None) -> tuple[int, str, bool]:
    if not raw:
        return 10, "RoHS status not stated by any source", True
    s = raw.strip().lower()
    # ORDER MATTERS: "non-rohs compliant" contains "rohs compliant"
    if "non" in s and "rohs" in s:
        return 40, f"NOT RoHS compliant (source says: '{raw}') — " \
                   f"unusable in EU-bound products without exemption", False
    if "exempt" in s:
        return 8, f"RoHS compliant by exemption ('{raw}') — exemptions " \
                  f"expire and must be tracked", False
    if "compliant" in s or "rohs3" in s or "rohs 3" in s:
        return 0, f"RoHS compliant ('{raw}')", False
    return 10, f"RoHS status unclear ('{raw}')", True


def _reach(raw: str | None) -> tuple[int, str, bool]:
    if not raw:
        return 8, "REACH status not stated by any source", True
    s = raw.strip().lower()
    # ORDER MATTERS: "unaffected" contains "affected" — innocent first
    if "unaffected" in s or "compliant" in s or "not affected" in s:
        return 0, f"REACH unaffected ('{raw}')", False
    if "svhc" in s and ("contain" in s or "affected" in s):
        return 25, f"contains REACH SVHC ('{raw}') — declaration " \
                   f"obligations apply", False
    if "affected" in s:
        return 15, f"REACH affected ('{raw}')", False
    return 8, f"REACH status unclear ('{raw}')", True


def _eccn(raw: str | None) -> tuple[int, str, bool]:
    if not raw:
        return 5, "export control classification not stated", True
    s = raw.strip().upper()
    if s in ("EAR99", "EAR 99", "N/A", "NONE"):
        return 0, f"EAR99 ('{raw}') — no special export restrictions", False
    # anti-terrorism-only classifications: mild
    # judge the base classification: "5A992.c" -> "5A992"
    base = s.split(".")[0].strip()
    if base.endswith("992") or base.endswith("991"):
        return 8, f"ECCN {raw} — mildly controlled (AT-only class); " \
                  f"screening required for some destinations", False
    return 14, f"ECCN {raw} — export-controlled; destination and " \
               f"end-user screening required", False


def _msl(raw: str | None) -> tuple[int, str, bool]:
    if not raw:
        return 3, "moisture sensitivity level not stated", True
    s = raw.strip().lower()
    level = None
    for tok in ("5a", "6", "5", "4", "3", "2a", "2", "1"):
        if s.startswith(tok) or f"msl {tok}" in s or f"level {tok}" in s:
            level = tok
            break
    if level == "1":
        return 0, f"MSL 1 ('{raw}') — unlimited floor life", False
    if level in ("2", "2a", "3"):
        return 4, f"MSL {level} ('{raw}') — dry-pack handling, " \
                  f"moderate floor life", False
    if level in ("4", "5", "5a"):
        return 10, f"MSL {level} ('{raw}') — short floor life; " \
                   f"strict handling burden", False
    if level == "6":
        return 15, f"MSL 6 ('{raw}') — mandatory bake before use", False
    return 3, f"moisture sensitivity unclear ('{raw}')", True
