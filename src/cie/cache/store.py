# src/cie/cache/store.py
"""Two logical caches with different TTLs, both diskcache/SQLite-backed.

Static cache:       part identity, parametrics, datasheet URL (days).
Availability cache: offers per (source, mpn), stored WITH their fetch
                    timestamp so the pipeline can compute the record-level
                    availability_snapshot_ts and dedup by freshness (hours).
"""
from datetime import datetime, timezone
from typing import Any

from diskcache import Cache

from cie.config import Settings


class CacheStore:
    """Thin wrapper enforcing the static/availability split."""

    def __init__(self, settings: Settings) -> None:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self._static = Cache(str(settings.cache_dir / "static"))
        self._avail = Cache(str(settings.cache_dir / "availability"))
        self._static_ttl = settings.static_ttl_days * 86400
        self._avail_ttl = settings.availability_ttl_hours * 3600

    # --- static ---
    def get_static(self, key: str) -> Any | None:
        return self._static.get(key)

    def set_static(self, key: str, value: Any) -> None:
        self._static.set(key, value, expire=self._static_ttl)

    # --- availability (value + fetch timestamp travel together) ---
    def get_availability(self, key: str) -> tuple[Any, datetime] | None:
        """Returns (payload, fetched_at) or None on miss/expiry."""
        wrapped = self._avail.get(key)
        if wrapped is None:
            return None
        return wrapped["payload"], datetime.fromisoformat(wrapped["fetched_at"])

    def set_availability(self, key: str, payload: Any) -> datetime:
        now = datetime.now(timezone.utc)
        self._avail.set(
            key, {"payload": payload, "fetched_at": now.isoformat()},
            expire=self._avail_ttl,
        )
        return now
