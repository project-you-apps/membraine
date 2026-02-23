"""
Membraine Cache
In-memory LRU cache for fetched + processed pages.

Keyed by URL. Stores post-pipeline results (markdown, chunks, embeddings)
so different queries against the same URL don't re-fetch.
TTL-based expiry (default 15 minutes).
"""

import time
from dataclasses import dataclass, field
from collections import OrderedDict


@dataclass
class CacheEntry:
    """Cached result for a URL."""
    url: str
    title: str
    markdown: str
    chunks: list          # list of Chunk objects (with embeddings)
    threats: list         # list of Threat objects
    meta: dict
    timestamp: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


class MembraineCache:
    """
    LRU cache with TTL expiry for fetched pages.

    Usage:
        cache = MembraineCache(max_entries=100, ttl_seconds=900)
        cache.put(url, entry)
        entry = cache.get(url)  # None if expired or missing
    """

    def __init__(self, max_entries: int = 100, ttl_seconds: int = 900):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, url: str) -> CacheEntry | None:
        """Get cached entry, or None if missing/expired."""
        entry = self._store.get(url)
        if entry is None:
            return None
        if entry.age_seconds > self.ttl_seconds:
            del self._store[url]
            return None
        # Move to end (most recently accessed)
        self._store.move_to_end(url)
        return entry

    def put(self, url: str, entry: CacheEntry):
        """Store an entry, evicting oldest if at capacity."""
        if url in self._store:
            del self._store[url]
        self._store[url] = entry
        # Evict if over capacity
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def invalidate(self, url: str):
        """Remove a specific URL from cache."""
        self._store.pop(url, None)

    def clear(self):
        """Clear all cached entries."""
        self._store.clear()

    def cleanup_expired(self):
        """Remove all expired entries."""
        now = time.time()
        expired = [
            url for url, entry in self._store.items()
            if (now - entry.timestamp) > self.ttl_seconds
        ]
        for url in expired:
            del self._store[url]

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        """Cache statistics."""
        self.cleanup_expired()
        ages = [e.age_seconds for e in self._store.values()]
        return {
            "entries": len(self._store),
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "oldest_age_s": round(max(ages), 1) if ages else 0,
            "newest_age_s": round(min(ages), 1) if ages else 0,
        }
