# src/cie/lifecycle/datasheet_age.py
"""Best-effort datasheet revision-year extraction.

Manufacturers format revision dates a hundred different ways, so this is a
heuristic by design: scan the first pages and last page of the local PDF
for plausible years, keep the LATEST one at or before the current year
(datasheets cite their newest revision somewhere). Failure to find a date
returns None — reported as unknown, never guessed.
"""
import re
from datetime import datetime, timezone

_YEAR = re.compile(r"\b(19[9]\d|20[0-3]\d)\b")


def years_from_text(text: str) -> list[int]:
    now = datetime.now(timezone.utc).year
    return [int(y) for y in _YEAR.findall(text) if 1990 <= int(y) <= now]


def extract_datasheet_year(local_path: str | None) -> int | None:
    """Latest plausible revision year found in the PDF, or None."""
    if not local_path:
        return None
    try:
        import pdfplumber
        with pdfplumber.open(local_path) as pdf:
            pages = pdf.pages
            sample = pages[:2] + (pages[-1:] if len(pages) > 2 else [])
            years: list[int] = []
            for page in sample:
                years.extend(years_from_text(page.extract_text() or ""))
        return max(years) if years else None
    except Exception:   # unreadable/corrupt PDF -> unknown, not an error
        return None
