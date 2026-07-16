# src/cie/substitution/engine.py
"""M5 orchestration: gather candidates, run each through the ENTIRE
existing engine (M1 lookup -> M2/M3/M4 scores), judge fit, rank.

Candidate sources (approved design):
  1. px4_alternates — an optional user-supplied file `alternates.txt`
     next to the MPN list: lines of `reference_mpn, alternate_mpn`.
     We never invent alternates; no file, no seed — stated honestly.
  2. digikey_discovery — a keyword search built from the reference
     part's own sacred parameters (per fit_rules.json query_params).

Ranking (approved): fit is a GATE, then ascending composite health
(0.5 lifecycle + 0.25 manufacturer + 0.25 compliance; lower = better).
Rejected candidates are reported with their failing evidence — a
rejection with reasons is information, not garbage.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from cie.compliance.scorer import score_compliance
from cie.ingestion.pipeline import IngestionPipeline, MpnNotFoundError
from cie.lifecycle.scorer import score_lifecycle
from cie.manufacturer.scorer import score_manufacturer
from cie.models.part import PartRecord
from cie.substitution.fit import FitVerdict, check_fit, rules_for
from cie.substitution.models import SubstituteCandidate, SubstitutionReport

logger = logging.getLogger(__name__)

MAX_SCORED_CANDIDATES = 6   # quota courtesy: cap full lookups per report


def _query_term(value: str) -> str:
    """Make a parametric value search-safe: '100 µF' -> '100uF'.
    The micro sign is the trap: Python uppercases µ to the Greek
    capital Μ, which no search engine associates with capacitors."""
    v = value.split("(")[0].strip()
    # BOTH mus: micro sign U+00B5 and Greek small mu U+03BC look identical
    # on screen; either uppercases into Greek capital Mu and breaks search
    v = v.replace("\u00b5", "u").replace("\u03bc", "u")
    v = v.replace("\u00b0", "")            # degree sign in temp ranges
    v = v.encode("ascii", "ignore").decode()  # nothing exotic survives
    # glue a number to its unit: '100 uF' -> '100uF', '4 V' -> '4V'
    import re
    v = re.sub(r"(?<=\d)\s+(?=[a-zA-Z])", "", v)
    return v.strip()


def load_alternates(path: Path, reference_mpn: str) -> list[str]:
    """Optional `alternates.txt`: lines of `reference_mpn, alternate_mpn`."""
    if not path.exists():
        return []
    out = []
    ref_norm = reference_mpn.strip().upper()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "," not in line:
            continue
        ref, alt = (p.strip() for p in line.split(",", 1))
        if ref.upper() == ref_norm and alt:
            out.append(alt)
    return out


def discover_candidates(pipeline: IngestionPipeline,
                        reference: PartRecord) -> list[str]:
    """Digi-Key keyword discovery from the reference's sacred parameters."""
    rules = rules_for(reference.category.value)
    terms = []
    for param in rules.get("query_params", []):
        v = reference.parametrics.get(param)
        if v and v.strip() not in ("-", ""):
            terms.append(_query_term(v))
    if not terms:
        logger.info("discovery skipped: reference lacks the parametrics "
                    "needed to build a query")
        return []
    query = " ".join(terms + [rules.get("query_suffix", "")]).strip()
    logger.info("discovery query: %r", query)
    try:
        candidates, _ = pipeline._digikey.search(query)
    except Exception as exc:
        logger.warning("discovery search failed: %s", exc)
        return []
    logger.info("discovery returned %d candidate(s): %s",
                len(candidates), [c.mpn for c in candidates])
    return [c.mpn for c in candidates]


def build_report(pipeline: IngestionPipeline, reference: PartRecord,
                 alternates_file: Path | None = None) -> SubstitutionReport:
    rules = rules_for(reference.category.value)
    if not rules.get("auto_substitution", False):
        return SubstitutionReport(
            reference_mpn=reference.mpn,
            reference_category=reference.category.value,
            generated_at=datetime.now(timezone.utc),
            policy_block=rules.get("policy_reason", "no auto-substitution"),
            ranked=[], unverified=[], rejected=[])

    seen: set[str] = {reference.mpn.upper()}
    sourced: list[tuple[str, str]] = []
    if alternates_file is not None:
        for alt in load_alternates(alternates_file, reference.query_mpn):
            if alt.upper() not in seen:
                seen.add(alt.upper())
                sourced.append((alt, "px4_alternates"))
    for mpn in discover_candidates(pipeline, reference):
        if mpn.upper() not in seen:
            seen.add(mpn.upper())
            sourced.append((mpn, "digikey_discovery"))

    scored: list[SubstituteCandidate] = []
    for mpn, source in sourced[:MAX_SCORED_CANDIDATES]:
        try:
            rec = pipeline.run(mpn)
            fit, evidence, unknowns = check_fit(
                reference.category.value, reference.parametrics,
                rec.parametrics)
            life = score_lifecycle(rec)
            mfr = score_manufacturer(rec)
            comp = score_compliance(rec)
            composite = round(0.5 * life.score + 0.25 * mfr.score
                              + 0.25 * comp.score, 1)
            scored.append(SubstituteCandidate(
                mpn=rec.mpn, manufacturer=rec.manufacturer, source=source,
                fit=fit, fit_evidence=evidence, fit_unknowns=unknowns,
                lifecycle_score=life.score, manufacturer_score=mfr.score,
                compliance_score=comp.score, composite=composite,
                total_stock=sum(o.stock_qty for o in rec.offers)))
        except MpnNotFoundError:
            scored.append(SubstituteCandidate(
                mpn=mpn, manufacturer="?", source=source,
                fit=FitVerdict.UNVERIFIED, fit_evidence=[], fit_unknowns=[],
                error="not found at any configured source"))
        except Exception as exc:   # a bad candidate never kills the report
            scored.append(SubstituteCandidate(
                mpn=mpn, manufacturer="?", source=source,
                fit=FitVerdict.UNVERIFIED, fit_evidence=[], fit_unknowns=[],
                error=f"lookup failed: {exc}"))

    by_health = lambda c: (c.composite if c.composite is not None else 999.0)
    return SubstitutionReport(
        reference_mpn=reference.mpn,
        reference_category=reference.category.value,
        generated_at=datetime.now(timezone.utc),
        ranked=sorted([c for c in scored if c.fit == FitVerdict.FIT],
                      key=by_health),
        unverified=sorted([c for c in scored
                           if c.fit == FitVerdict.UNVERIFIED], key=by_health),
        rejected=[c for c in scored if c.fit == FitVerdict.REJECTED])
