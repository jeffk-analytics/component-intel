# scripts/probe_oemsecrets.py
"""Capture ONE live OEMsecrets response as a specimen for parser building.

    python scripts/probe_oemsecrets.py GRM21BR60G107ME15L

Prompts for the API key (hidden as you type; NOT stored anywhere) and
saves the raw response to oemsecrets_sample.json. Costs exactly 1 of the
daily part budget. Deliberately does NOT read the key from .env — the
key must stay out of .env until the response parser exists, because the
engine auto-promotes OEMsecrets to spine the moment it sees that key."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
import typer

app = typer.Typer(add_completion=False)

_URL = "https://oemsecretsapi.com/partsearch"   # VERIFY — probe will tell us


@app.command()
def main(mpn: str):
    key = typer.prompt("OEMsecrets API key (hidden)", hide_input=True).strip()
    typer.echo(f"Asking OEMsecrets about {mpn} (1 of today's part budget)...")
    try:
        resp = httpx.get(_URL, params={
            "apiKey": key,
            "searchTerm": mpn.strip(),
            "countryCode": "US",
            "currencyCode": "USD",
        }, timeout=30)
    except Exception as exc:
        typer.echo(f"Request failed before reaching the server: {exc}")
        raise typer.Exit(1)

    typer.echo(f"HTTP status: {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        typer.echo("Response was not JSON. First 500 characters:")
        typer.echo(resp.text[:500])
        raise typer.Exit(1)

    out = Path("oemsecrets_sample.json")
    out.write_text(json.dumps(body, indent=2), encoding="utf-8")
    typer.echo(f"Top-level keys: {list(body.keys()) if isinstance(body, dict) else type(body).__name__}")
    if isinstance(body, dict):
        for k, v in body.items():
            size = len(v) if isinstance(v, (list, dict, str)) else v
            typer.echo(f"  - {k}: {type(v).__name__} ({size})")
    typer.echo(f"\nSpecimen saved to: {out}")
    typer.echo("Upload that file to the chat — the parser gets built from it.")
    typer.echo("(Skim it first if you like: it should contain only public "
               "part/pricing data, no account information.)")


if __name__ == "__main__":
    app()
