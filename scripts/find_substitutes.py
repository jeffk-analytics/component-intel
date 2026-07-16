# scripts/find_substitutes.py
"""M5 on demand: substitution report for ONE part number.

    python scripts/find_substitutes.py GRM21BR60G107ME15L

Deliberately separate from the 10-part scorecard: each candidate costs
real lookups, so substitution runs when a human asks, not on every scan.
Optional seed file `alternates.txt` (same folder as the MPN list):
lines of `reference_mpn, alternate_mpn`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URLs can carry API keys

import typer

from cie.ingestion.pipeline import IngestionPipeline, MpnNotFoundError
from cie.substitution.engine import build_report

app = typer.Typer(add_completion=False)


def _line(c, show_fit_lines: bool) -> None:
    health = (f"life {c.lifecycle_score} | mfr {c.manufacturer_score} | "
              f"cmp {c.compliance_score} | composite {c.composite}"
              if c.composite is not None else "not scored")
    stock = f", stock {c.total_stock:,}" if c.total_stock is not None else ""
    typer.echo(f"  {c.mpn}  ({c.manufacturer}; via {c.source})")
    if c.error:
        typer.echo(f"      ! {c.error}")
    else:
        typer.echo(f"      {health}{stock}")
    if show_fit_lines:
        for e in c.fit_evidence:
            typer.echo(f"      - {e}")


@app.command()
def main(mpn: str,
         alternates: Path = typer.Option(None, help="Optional alternates.txt seed file"),
         verbose: bool = typer.Option(False, "--verbose", "-v",
                                      help="Show per-parameter fit evidence for every candidate")):
    """Build a ranked substitution report for MPN."""
    pipeline = IngestionPipeline()
    typer.echo(f"Resolving reference part {mpn} ...")
    try:
        reference = pipeline.run(mpn)
    except MpnNotFoundError:
        typer.echo("Reference part not found at any configured source.")
        raise typer.Exit(1)

    typer.echo(f"Reference: {reference.mpn} ({reference.manufacturer}), "
               f"category: {reference.category.value}")
    typer.echo(f"Reference parametrics on record: {len(reference.parametrics)}"
               + ("" if reference.parametrics else
                  "  <-- discovery needs these; if 0, run "
                  "scripts/inspect_part.py on this MPN"))
    report = build_report(pipeline, reference, alternates_file=alternates)

    if report.policy_block:
        typer.echo(f"\nNo automatic substitution for this category:")
        typer.echo(f"  {report.policy_block}")
    else:
        typer.echo(f"\nQUALIFIED SUBSTITUTES ({len(report.ranked)}) — "
                   f"fit verified, ranked by health:")
        if not report.ranked:
            typer.echo("  (none)")
        for c in report.ranked:
            _line(c, show_fit_lines=True)
        typer.echo(f"\nUNVERIFIED ({len(report.unverified)}) — nothing "
                   f"violated, but gaps prevent verification:")
        if not report.unverified:
            typer.echo("  (none)")
        for c in report.unverified:
            _line(c, show_fit_lines=verbose)
        typer.echo(f"\nREJECTED ({len(report.rejected)}) — failed a sacred "
                   f"parameter:")
        if not report.rejected:
            typer.echo("  (none)")
        for c in report.rejected:
            _line(c, show_fit_lines=verbose)

    out = Path(f"{mpn.replace('/', '_')}.substitutes.json")
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"\nFull report saved to: {out}")


if __name__ == "__main__":
    app()
