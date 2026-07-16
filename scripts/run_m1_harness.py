# scripts/run_m1_harness.py
"""Phase-1 test scorecard for the M1 lookup engine.

Reads a plain text file of part numbers (one per line, '#' lines are
comments), runs each through the ingestion pipeline, and prints:
  * resolution success rate
  * distributor offers found per part, split by source
    (direct store call vs Nexar second-hand)
  * for parts with zero stock everywhere: whether any usable
    lead-time data was stated by a source
Also saves the full results as JSON next to the input file for audit.

Usage:
    python scripts/run_m1_harness.py mpns.txt
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the project importable even without `pip install -e .`:
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import typer

from cie.ingestion.pipeline import IngestionPipeline, MpnNotFoundError
from cie.lifecycle.history import append_snapshot
from cie.lifecycle.scorer import score_lifecycle
from cie.manufacturer.observations import record_observation, stats_for
from cie.manufacturer.scorer import score_manufacturer
from cie.compliance.scorer import score_compliance
from cie.models.enums import OfferSource
from cie.models.part import PartRecord

app = typer.Typer(add_completion=False)


def _load_mpns(path: Path) -> list[str]:
    """One part number per line; blank lines and '#' comments ignored."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _summarize(record: PartRecord, life=None, mfr=None, comp=None) -> dict:
    """Boil one part's record down to the scorecard numbers."""
    direct = [o for o in record.offers if o.source == OfferSource.DIRECT_API]
    relayed = [o for o in record.offers if o.source != OfferSource.DIRECT_API]
    total_stock = sum(o.stock_qty for o in record.offers)
    out_of_stock = bool(record.offers) and total_stock == 0
    has_lead_time = (
        any(o.lead_time_days is not None for o in record.offers)
        or record.factory_lead_days is not None
    )
    import re as _re
    _strip = lambda s: _re.sub(r"[^A-Z0-9]", "", s.upper())
    weak_match = not _strip(record.mpn).startswith(_strip(record.query_mpn)[:6])
    return {
        "query_mpn": record.query_mpn,
        "resolved_mpn": record.mpn,
        "resolution_warning": ("resolved part differs substantially from the "
                               "query — review alternates_considered"
                               ) if weak_match else None,
        "lifecycle_status": record.lifecycle_status.value,
        "lifecycle_score": life.score if life else None,
        "risk_band": life.band.value if life else None,
        "score_reasons": ([f"{r.signal}: +{r.points} ({r.detail})"
                           for r in life.reasons] if life else []),
        "score_unknowns": (life.unknowns if life else []),
        "manufacturer_score": mfr.score if mfr else None,
        "manufacturer_band": mfr.band.value if mfr else None,
        "manufacturer_canonical": mfr.canonical if mfr else None,
        "manufacturer_reasons": ([f"{r.signal}: +{r.points} ({r.detail})"
                                  for r in mfr.reasons] if mfr else []),
        "manufacturer_observed_sample": mfr.observed_sample if mfr else 0,
        "compliance_score": comp.score if comp else None,
        "compliance_band": comp.band.value if comp else None,
        "compliance_reasons": ([f"{r.signal}: +{r.points} ({r.detail})"
                                for r in comp.reasons] if comp else []),
        "compliance_unknowns": (comp.unknowns if comp else []),
        "manufacturer": record.manufacturer,
        "category": record.category.value,
        "offers_total": len(record.offers),
        "offers_direct": len(direct),
        "offers_aggregated": len(relayed),
        "total_stock": total_stock,
        "out_of_stock_everywhere": out_of_stock,
        "stated_lead_time_available": has_lead_time,
        "datasheet_downloaded": bool(record.datasheet and record.datasheet.local_path),
        "sources_failed": record.meta.sources_failed,
    }


@app.command()
def main(
    mpn_file: Path = typer.Argument(..., help="Text file: one part number per line"),
) -> None:
    """Run the M1 scorecard against a list of part numbers."""
    if not mpn_file.exists():
        typer.echo(f"File not found: {mpn_file}")
        raise typer.Exit(code=1)

    mpns = _load_mpns(mpn_file)
    if not mpns:
        typer.echo("No part numbers found in the file.")
        raise typer.Exit(code=1)

    pipeline = IngestionPipeline()
    results: list[dict] = []
    failures: list[dict] = []

    typer.echo(f"\nRunning {len(mpns)} part numbers through the lookup engine...\n")

    for i, mpn in enumerate(mpns, start=1):
        typer.echo(f"[{i}/{len(mpns)}] {mpn} ... ", nl=False)
        try:
            record = pipeline.run(mpn)
        except MpnNotFoundError:
            typer.echo("NOT FOUND")
            failures.append({"query_mpn": mpn, "reason": "not found"})
            continue
        except Exception as exc:  # any other problem: log it, keep going
            typer.echo(f"ERROR ({type(exc).__name__})")
            failures.append({"query_mpn": mpn, "reason": str(exc)})
            continue
        snaps = append_snapshot(record, pipeline.settings)
        life = score_lifecycle(record, history_snapshots=snaps)
        mfr_probe = score_manufacturer(record)          # canonical name only
        record_observation(record, mfr_probe.canonical, pipeline.settings)
        n, frac = stats_for(mfr_probe.canonical or record.manufacturer,
                            pipeline.settings)
        mfr = score_manufacturer(record, observed_sample=n,
                                 observed_dead_fraction=frac)
        comp = score_compliance(record)
        summary = _summarize(record, life, mfr, comp)
        results.append(summary)
        flag = "  ⚠ WEAK MATCH" if summary.get("resolution_warning") else ""
        flag += (f"  [{life.band.value.upper().replace('_', ' ')} "
                 f"{life.score} | mfr {mfr.score} | cmp {comp.score}]")
        typer.echo(
            f"ok — {summary['offers_total']} offers "
            f"({summary['offers_direct']} direct, "
            f"{summary['offers_aggregated']} via aggregator)" + flag
        )

    # ---- the report card ----
    total = len(mpns)
    resolved = len(results)
    oos = [r for r in results if r["out_of_stock_everywhere"]]
    oos_with_lead = [r for r in oos if r["stated_lead_time_available"]]

    typer.echo("\n" + "=" * 60)
    typer.echo("SCORECARD")
    typer.echo("=" * 60)
    typer.echo(f"Parts identified successfully : {resolved} of {total}")
    if results:
        avg = sum(r["offers_total"] for r in results) / resolved
        typer.echo(f"Average store listings per part: {avg:.1f}")
        typer.echo(f"  ...from direct store calls   : "
                   f"{sum(r['offers_direct'] for r in results)}")
        typer.echo(f"  ...via aggregator (2nd-hand) : "
                   f"{sum(r['offers_aggregated'] for r in results)}")
        typer.echo(f"Datasheets downloaded          : "
                   f"{sum(1 for r in results if r['datasheet_downloaded'])} of {resolved}")
    bands = {}
    for r in results:
        bands[r["risk_band"]] = bands.get(r["risk_band"], 0) + 1
    if bands:
        pretty = ", ".join(f"{k.replace('_',' ')}: {v}"
                           for k, v in sorted(bands.items()))
        typer.echo(f"Lifecycle risk bands           : {pretty}")
    comp_flags = [r for r in results if (r.get("compliance_score") or 0) >= 25]
    typer.echo(f"Compliance flags (>=25)        : {len(comp_flags)}"
               + (" — " + ", ".join(r["query_mpn"] for r in comp_flags)
                  if comp_flags else ""))
    mfr_flags = [r for r in results if (r.get("manufacturer_score") or 0) >= 25]
    typer.echo(f"Manufacturer risk flags (>=25) : {len(mfr_flags)}"
               + (" — " + ", ".join(r["query_mpn"] for r in mfr_flags)
                  if mfr_flags else ""))
    typer.echo(f"Parts out of stock everywhere  : {len(oos)}")
    if oos:
        typer.echo(f"  ...with a stated lead time   : {len(oos_with_lead)}")
        typer.echo(f"  ...with NO stated lead time  : {len(oos) - len(oos_with_lead)}"
                   f"  (reported as-is — a risk signal, never guessed)")
    if failures:
        typer.echo(f"\nProblems ({len(failures)}):")
        for f in failures:
            typer.echo(f"  {f['query_mpn']}: {f['reason']}")

    # ---- full details saved for the record ----
    out_path = mpn_file.with_suffix(".results.json")
    out_path.write_text(json.dumps(
        {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "failures": failures,
        },
        indent=2,
    ), encoding="utf-8")
    typer.echo(f"\nFull details saved to: {out_path}\n")


if __name__ == "__main__":
    app()
