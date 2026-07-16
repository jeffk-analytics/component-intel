# src/cie/manufacturer/observations.py
"""Cumulative ledger of every part we have ever scanned, grouped by
manufacturer — the raw material for the observed-pruning signal
(approved: include now, with honest small-sample disclosure).

data/manufacturer/observations.jsonl — one line per (day, part); on load,
the LATEST line per part wins, so re-scans update rather than double-count.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from cie.config import Settings
from cie.models.part import PartRecord


def _path(settings: Settings) -> Path:
    d = settings.data_dir / "manufacturer"
    d.mkdir(parents=True, exist_ok=True)
    return d / "observations.jsonl"


def record_observation(record: PartRecord, canonical: str | None,
                       settings: Settings) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mpn": record.mpn,
        "manufacturer": record.manufacturer,
        "canonical": canonical or record.manufacturer,
        "lifecycle_status": record.lifecycle_status.value,
    }
    with open(_path(settings), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def stats_for(canonical: str, settings: Settings) -> tuple[int, float]:
    """(sample size, fraction of scanned parts that are dead/dying)."""
    path = _path(settings)
    if not path.exists():
        return 0, 0.0
    latest: dict[str, dict] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        latest[row.get("mpn", "")] = row
    mine = [r for r in latest.values() if r.get("canonical") == canonical]
    if not mine:
        return 0, 0.0
    dead = sum(1 for r in mine
               if r.get("lifecycle_status") in ("obsolete", "eol", "nrnd"))
    return len(mine), dead / len(mine)
