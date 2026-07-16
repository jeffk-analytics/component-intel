# scripts/inspect_part.py
"""X-ray one part's canonical record — every field, honestly.

    python scripts/inspect_part.py GRM21BR60G107ME15L

Reads through the normal pipeline (cache-first, so usually free)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URLs can carry API keys

import typer

from cie.ingestion.pipeline import IngestionPipeline, MpnNotFoundError

app = typer.Typer(add_completion=False)


@app.command()
def main(mpn: str):
    pipeline = IngestionPipeline()
    try:
        r = pipeline.run(mpn)
    except MpnNotFoundError:
        typer.echo("Not found at any configured source.")
        raise typer.Exit(1)
    typer.echo(f"query_mpn      : {r.query_mpn}")
    typer.echo(f"resolved mpn   : {r.mpn}")
    typer.echo(f"manufacturer   : {r.manufacturer}")
    typer.echo(f"category       : {r.category.value}  (source: {r.category_source})")
    typer.echo(f"lifecycle      : {r.lifecycle_status.value}  (raw: {r.lifecycle_status_raw!r})")
    typer.echo(f"spine_part_id  : {r.spine_part_id}")
    typer.echo(f"schema_version : {r.schema_version}")
    c = r.compliance
    typer.echo(f"compliance     : " + (f"rohs={c.rohs_raw!r} reach={c.reach_raw!r} "
               f"eccn={c.eccn_raw!r} msl={c.msl_raw!r} (source {c.source})"
               if c else "None"))
    typer.echo(f"datasheet      : " + (f"{r.datasheet.url} "
               f"(downloaded: {bool(r.datasheet.local_path)})" if r.datasheet else "None"))
    typer.echo(f"offers         : {len(r.offers)}")
    for o in r.offers:
        lead = f"{o.lead_time_days}d" if o.lead_time_days is not None else "unstated"
        typer.echo(f"  - {o.distributor_name}: stock {o.stock_qty}, "
                   f"moq {o.moq}, lead {lead}, src {o.source.value}")
    typer.echo(f"PARAMETRICS    : {len(r.parametrics)} captured")
    for k, v in list(r.parametrics.items())[:25]:
        typer.echo(f"  - {k!r}: {v!r}")
    if not r.parametrics:
        typer.echo("  (empty — if Digi-Key's website shows a specs table for "
                   "this part, the harvest is misreading the response format)")


if __name__ == "__main__":
    app()
