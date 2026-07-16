# Component Intelligence Engine — Phase 1 (M1 ingestion)

Give it an electronic part number; it gathers identity, lifecycle status,
stock, pricing, lead times, and the datasheet, then reports sourcing risk
inputs. Phase 1 covers the lookup engine and a 10-part test scorecard.

## Data sources (current structure — Nexar removed)

Spine (identifies the part), chosen automatically at startup:
  1. OEMsecrets  — preferred free aggregator (140+ distributors), used the
                   moment CIE_OEMSECRETS_API_KEY is filled in
  2. Digi-Key    — free direct API, acts as spine until then

Enrichment: Mouser (active), Arrow and Avnet (auto-enable when keys arrive).
A blank line in .env means that source is OFF — never broken.

## First-time setup

1. Install Python 3.11+ from python.org (one-time, per machine).
2. Open PowerShell and move into THIS folder (the one containing this file):
       cd path\to\component-intel
3. Install building blocks (one-time, per machine):
       pip install -r requirements.txt
4. Copy `.env.example`, rename the copy to `.env`, and fill in your keys.
   Minimum to run today: the two Digi-Key lines and the Mouser line.
5. Your 10 test part numbers live in `mpns.txt` (edit if desired).

## Running the scorecard

       python scripts/run_m1_harness.py mpns.txt

Full per-part details are saved next to the list as `mpns.results.json`.
Run it twice: the second run demonstrates the local cache (near-instant,
no API quota spent).

## Reading the results

- "via aggregator" = 0 is expected while Digi-Key is the spine.
- "no stated lead time" is reported honestly as a risk signal — the
  engine never invents or estimates a lead time.
- A NOT FOUND means no configured source carries that exact number —
  itself a coverage data point.
