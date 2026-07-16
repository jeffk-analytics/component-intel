# src/cie/ingestion/datasheets.py
"""Datasheet PDF retrieval. Best-effort: a failed download never fails the
pipeline — DatasheetRef.local_path stays None and the URL is preserved."""
import re
from datetime import datetime, timezone

import httpx

from cie.config import Settings
from cie.ingestion.clients.common import normalize_url
from cie.models.part import DatasheetRef

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def fetch_datasheet(url: str | None, mpn: str, settings: Settings) -> DatasheetRef | None:
    """Download the datasheet PDF to data/datasheets/<safe-mpn>.pdf."""
    url = normalize_url(url)
    if not url:
        return None
    settings.datasheet_dir.mkdir(parents=True, exist_ok=True)
    target = settings.datasheet_dir / f"{_SAFE.sub('_', mpn)}.pdf"
    if target.exists():
        return DatasheetRef(url=url, local_path=str(target),
                            retrieved_at=datetime.now(timezone.utc))
    try:
        with httpx.stream("GET", url, follow_redirects=True,
                          timeout=settings.http_timeout_seconds) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            first = b""
            with open(target, "wb") as fh:
                for chunk in resp.iter_bytes():
                    if not first:
                        first = chunk[:5]
                    fh.write(chunk)
            if "pdf" not in ctype and not first.startswith(b"%PDF"):
                target.unlink(missing_ok=True)   # HTML interstitial, not a PDF
                return DatasheetRef(url=url)
        return DatasheetRef(url=url, local_path=str(target),
                            retrieved_at=datetime.now(timezone.utc))
    except httpx.HTTPError:
        return DatasheetRef(url=url)
