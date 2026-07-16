# scripts/try_search.py
"""Probe Digi-Key's keyword search directly: see what a query returns.

    python scripts/try_search.py "100uF 0805 X5R"

Prints how many products came back and the identity line of each —
the empirical way to learn what the search dialect wants."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)  # URLs can carry API keys

import typer

from cie.cache.store import CacheStore
from cie.config import get_settings
from cie.ingestion.clients.digikey import DigiKeyClient

app = typer.Typer(add_completion=False)


@app.command()
def main(query: str):
    settings = get_settings()
    dk = DigiKeyClient(settings, CacheStore(settings))
    raw = dk._keyword_search(query.strip().upper())
    products = raw.get("Products") or []
    count_field = raw.get("ProductsCount")
    typer.echo(f"Query        : {query!r}")
    typer.echo(f"ProductsCount: {count_field!r}   Products returned: {len(products)}")
    for prod in products[:10]:
        mpn = (prod.get("ManufacturerProductNumber")
               or prod.get("ManufacturerPartNumber") or "?")
        mfr = (prod.get("Manufacturer") or {}).get("Name", "?")
        desc = prod.get("Description")
        desc = (desc.get("ProductDescription") if isinstance(desc, dict)
                else desc) or ""
        typer.echo(f"  - {mpn}  ({mfr})  {desc[:60]}")
    if not products:
        typer.echo("  (empty — this phrasing found nothing)")
        other_keys = [k for k in raw.keys() if k not in ("Products", "ProductsCount")]
        typer.echo(f"  other response keys present: {other_keys}")


if __name__ == "__main__":
    app()
