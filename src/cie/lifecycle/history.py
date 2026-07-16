# src/cie/lifecycle/history.py
"""Local availability history — the free time machine (approved 2026-07).

Every scored run appends one dated line per part to
data/history/<mpn>.jsonl. After a few weeks of runs, genuine stock TRENDS
become computable from this file. Until then it just accumulates quietly.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cie.config import Settings
from cie.models.part import PartRecord

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _path(mpn: str, settings: Settings) -> Path:
    d = settings.data_dir / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_SAFE.sub('_', mpn.upper())}.jsonl"


def append_snapshot(record: PartRecord, settings: Settings) -> int:
    """Append today's availability facts; returns total snapshots on file.

    At most one snapshot per calendar day per part — re-running the harness
    five times in an afternoon should not fabricate five days of 'history'.
    """
    path = _path(record.mpn, settings)
    existing = load_snapshots(record.mpn, settings)
    today = datetime.now(timezone.utc).date().isoformat()
    if any(s.get("ts", "").startswith(today) for s in existing):
        return len(existing)

    stocked = {o.distributor_id for o in record.offers if o.stock_qty > 0}
    leads = [o.lead_time_days for o in record.offers
             if o.lead_time_days is not None]
    snap = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "total_stock": sum(o.stock_qty for o in record.offers),
        "distributors_stocking": len(stocked),
        "offers": len(record.offers),
        "min_lead_days": min(leads) if leads else None,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap) + "\n")
    return len(existing) + 1


def load_snapshots(mpn: str, settings: Settings) -> list[dict]:
    path = _path(mpn, settings)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
