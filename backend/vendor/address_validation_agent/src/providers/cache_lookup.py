"""
Address validation cache.

Design:
- Cache key = normalized_full_address (canonical form, lowercase, no spaces)
  This ensures "123 Main St" and "123 MAIN ST" always hit the same entry.
- Full result data stored per entry so cache hits reconstruct a complete
  FinalValidationRecord without any API call.
- The AddressCache object is loaded ONCE at pipeline startup and saved ONCE
  at the end — no per-address file I/O.
- Timestamps and hit counts are tracked for observability.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_FILE = Path("cache/cache.json")


def _normalize(text: str) -> str:
    """Lowercase and strip all whitespace / punctuation for a stable key."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def generate_cache_key(canonical) -> str:
    """
    Primary key: canonical normalized_full_address.
    Falls back to raw_address+city+state+zip if normalized form is empty
    (matches legacy behaviour).
    """
    base = canonical.normalized_full_address or (
        f"{canonical.raw_address}"
        f"{canonical.city or ''}"
        f"{canonical.state or ''}"
        f"{canonical.zip_code or ''}"
    )
    return _normalize(base)


class AddressCache:
    """
    In-memory address validation cache backed by a JSON file.

    Usage:
        cache = AddressCache()
        cache.load()

        key = generate_cache_key(canonical)
        entry = cache.get(key)
        if entry:
            # cache hit — use entry data
        else:
            # call APIs, then:
            cache.set(key, full_result_dict)

        cache.save()        # call once at end of run
        cache.print_stats()
    """

    def __init__(self, cache_file: Path = CACHE_FILE):
        self._file = cache_file
        self._data: Dict[str, Any] = {}
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> "AddressCache":
        """Load cache from disk. Returns self for chaining."""
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        return self

    def save(self) -> None:
        """Persist the in-memory cache to disk (call once per run)."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, default=str)

    # ------------------------------------------------------------------
    # Lookup / write
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._data.get(key)
        if entry:
            self._hits += 1
            # Increment per-entry hit counter
            entry["hit_count"] = entry.get("hit_count", 0) + 1
        else:
            self._misses += 1
        return entry

    def set(self, key: str, result: Dict[str, Any]) -> None:
        """
        Store a full validation result.  ``result`` should contain all fields
        needed to reconstruct a FinalValidationRecord (see run_pipeline.py).
        A ``cached_at`` timestamp is added automatically.
        """
        result = dict(result)  # copy so we don't mutate the caller's dict
        result.setdefault("cached_at", datetime.now(timezone.utc).isoformat())
        result.setdefault("hit_count", 0)
        self._data[key] = result

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        return {
            "total_entries": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "api_calls_saved": self._hits,
        }

    def print_stats(self) -> None:
        s = self.stats()
        print(
            f"\n{'='*50}\n"
            f"  CACHE STATS\n"
            f"  Entries in cache : {s['total_entries']}\n"
            f"  Cache hits       : {s['hits']}  (API calls saved)\n"
            f"  Cache misses     : {s['misses']} (fresh API calls made)\n"
            f"{'='*50}"
        )


# ---------------------------------------------------------------------------
# Legacy helpers — kept for backward compatibility with any code that
# calls the old module-level functions directly.
# ---------------------------------------------------------------------------

def load_cache() -> Dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    with open(CACHE_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache_data: Dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(cache_data, fh, indent=2, default=str)


def get_cached_result(cache_key: str) -> Optional[Dict[str, Any]]:
    return load_cache().get(cache_key)


def save_cached_result(cache_key: str, result: Dict[str, Any]) -> None:
    cache_data = load_cache()
    cache_data[cache_key] = result
    save_cache(cache_data)
