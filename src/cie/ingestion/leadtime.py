# src/cie/ingestion/leadtime.py
"""Lead-time normalization. One rule: parse what a source states, tag its
semantics, keep the verbatim string, and NEVER invent a value. Anything
unparseable normalizes to None with the raw string preserved for audit."""
import re

_WEEKS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:weeks?|wks?)\s*$", re.IGNORECASE)
_DAYS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:days?)?\s*$", re.IGNORECASE)


def parse_lead_time_to_days(raw: str | int | float | None) -> int | None:
    """Normalize a stated lead time to integer calendar days.

    Accepts ints/floats (assumed days), '84 Days', '12 weeks', '12 wks'.
    Returns None for anything absent, zero-ish-but-unstated, or unparseable
    — an unknown lead time is reported as unknown, not estimated.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    text = raw.strip()
    if not text:
        return None
    if m := _WEEKS.match(text):
        return round(float(m.group(1)) * 7)
    if m := _DAYS.match(text):
        val = round(float(m.group(1)))
        return val if val > 0 else None
    return None


def weeks_to_days(weeks: int | float | str | None) -> int | None:
    """For sources that report a field explicitly in weeks (Digi-Key).
    Accepts numbers OR numeric text ("12") — APIs are inconsistent about
    which they send. Anything non-numeric or non-positive -> None."""
    if weeks is None:
        return None
    try:
        w = float(str(weeks).strip())
    except ValueError:
        return None
    if w <= 0:
        return None
    return round(w * 7)
