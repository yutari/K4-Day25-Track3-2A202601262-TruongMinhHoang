from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from threading import RLock
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """Simple in-memory cache skeleton.

    TODO(student): Add a better semantic similarity function and false-hit guardrails.
    Use the module-level _is_uncacheable() and _looks_like_false_hit() helpers in your
    get() and set() methods.  For production, replace with SharedRedisCache.
    """

    def __init__(self, ttl_seconds: int, similarity_threshold: float):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []
        self.false_hit_log: list[dict[str, object]] = []
        self._lock = RLock()

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response by semantic similarity.

        TODO(student): Implement cache lookup with guardrails:
        1. Return (None, 0.0) if _is_uncacheable(query) — privacy check
        2. Evict expired entries (compare time.time() - created_at vs ttl_seconds)
        3. Find best matching entry using self.similarity(query, entry.key)
        4. If best_score >= similarity_threshold:
           a. Check _looks_like_false_hit(query, best_key) — if true, log to
              self.false_hit_log and return (None, best_score)
           b. Otherwise return (best_value, best_score)
        5. Return (None, best_score) if no match above threshold

        You'll need a self.false_hit_log: list[dict[str, object]] attribute
        (add it in __init__).
        """
        value, score, _metadata = self.get_with_metadata(query)
        return value, score

    def get_with_metadata(self, query: str) -> tuple[str | None, float, dict[str, str]]:
        """Return a cache match together with the metadata used for cost accounting."""
        if _is_uncacheable(query):
            return None, 0.0, {}

        with self._lock:
            now = time.time()
            self._entries = [
                entry for entry in self._entries if now - entry.created_at <= self.ttl_seconds
            ]

            best_entry: CacheEntry | None = None
            best_score = 0.0
            for entry in self._entries:
                score = self.similarity(query, entry.key)
                if score > best_score:
                    best_entry = entry
                    best_score = score

            if best_entry is None or best_score < self.similarity_threshold:
                return None, best_score, {}

            if _looks_like_false_hit(query, best_entry.key):
                self.false_hit_log.append(
                    {
                        "query": query,
                        "cached_key": best_entry.key,
                        "score": best_score,
                        "reason": "date_or_number_mismatch",
                    }
                )
                return None, best_score, {}

            return best_entry.value, best_score, best_entry.metadata.copy()

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in cache.

        TODO(student): Implement with privacy guardrail:
        1. Return immediately if _is_uncacheable(query)
        2. Append a CacheEntry to self._entries
        """
        if _is_uncacheable(query):
            return

        with self._lock:
            self._entries = [entry for entry in self._entries if entry.key != query]
            self._entries.append(
                CacheEntry(
                    key=query,
                    value=value,
                    created_at=time.time(),
                    metadata=(metadata or {}).copy(),
                )
            )

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Compute semantic similarity between two strings.

        TODO(student): Implement cosine similarity over character n-grams + word tokens.
        The naive token-overlap (Jaccard) approach loses too much information.

        Suggested approach:
        1. If a == b, return 1.0
        2. Tokenize both strings: split into words + character n-grams (n=3)
           e.g., "hello world" → ["hello", "world", "hel", "ell", "llo", "wor", "orl", "rld"]
        3. Build Counter (bag-of-words) vectors from these tokens
        4. Compute cosine similarity: dot(a,b) / (|a| * |b|)

        Hint: Use collections.Counter and math.sqrt.
        Import them at the top of the file.
        """
        if a == b:
            return 1.0

        def tokens(text: str) -> list[str]:
            words = text.lower().split()
            result = list(words)
            for word in words:
                result.extend(word[index : index + 3] for index in range(len(word) - 2))
            return result

        counts_a = Counter(tokens(a))
        counts_b = Counter(tokens(b))
        if not counts_a or not counts_b:
            return 0.0

        dot_product = sum(
            counts_a[token] * counts_b[token] for token in counts_a.keys() & counts_b.keys()
        )
        magnitude_a = math.sqrt(sum(count * count for count in counts_a.values()))
        magnitude_b = math.sqrt(sum(count * count for count in counts_b.values()))
        return dot_product / (magnitude_a * magnitude_b)


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments.

    TODO(student): Implement the get() and set() methods using Redis commands
    so that cache state is shared across multiple gateway instances.

    Data model (suggested):
        Key    = "{prefix}{query_hash}"   (Redis String namespace)
        Value  = Redis Hash with fields:  "query", "response"
        TTL    = Redis EXPIRE (automatic cleanup — no manual eviction)

    For similarity lookup: SCAN all keys with self.prefix, HGET each entry's
    "query" field, compute similarity locally via ResponseCache.similarity().

    Provided helpers:
        _is_uncacheable(query)          — True if privacy-sensitive
        _looks_like_false_hit(q, key)   — True if 4-digit numbers differ
        self._query_hash(query)         — deterministic short hash for Redis key
        ResponseCache.similarity(a, b)  — reuse your improved similarity function
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._lock = RLock()
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        from redis.exceptions import RedisError

        try:
            return bool(self._redis.ping())
        except RedisError:
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis.

        TODO(student): Implement cache lookup.  Suggested steps:
        1. Return (None, 0.0) if _is_uncacheable(query)
        2. Build exact-match key: f"{self.prefix}{self._query_hash(query)}"
        3. Try self._redis.hget(key, "response") — if found return (response, 1.0)
        4. Otherwise self._redis.scan_iter(f"{self.prefix}*") to iterate all cached keys
        5. For each key, HGET "query" field and compute
           ResponseCache.similarity(query, cached_query)
        6. Track best match that is >= self.similarity_threshold
        7. Before returning a match, check _looks_like_false_hit(); if true,
           append to self.false_hit_log and return (None, best_score)
        """
        value, score, _metadata = self.get_with_metadata(query)
        return value, score

    def get_with_metadata(self, query: str) -> tuple[str | None, float, dict[str, str]]:
        """Return a Redis cache match and its persisted metadata."""
        if _is_uncacheable(query):
            return None, 0.0, {}

        exact_key = f"{self.prefix}{self._query_hash(query)}"
        exact_entry = self._redis.hgetall(exact_key)
        if exact_entry and exact_entry.get("response") is not None:
            return str(exact_entry["response"]), 1.0, self._decode_metadata(exact_entry)

        best_score = 0.0
        best_query: str | None = None
        best_response: str | None = None
        best_metadata: dict[str, str] = {}
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            entry = self._redis.hgetall(key)
            cached_query = entry.get("query")
            cached_response = entry.get("response")
            if cached_query is None or cached_response is None:
                continue
            cached_query = str(cached_query)
            score = ResponseCache.similarity(query, cached_query)
            if score > best_score:
                best_score = score
                best_query = cached_query
                best_response = str(cached_response)
                best_metadata = self._decode_metadata(entry)

        if best_response is None or best_score < self.similarity_threshold:
            return None, best_score, {}

        if best_query is not None and _looks_like_false_hit(query, best_query):
            with self._lock:
                self.false_hit_log.append(
                    {
                        "query": query,
                        "cached_key": best_query,
                        "score": best_score,
                        "reason": "date_or_number_mismatch",
                    }
                )
            return None, best_score, {}

        return best_response, best_score, best_metadata

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL.

        TODO(student): Implement cache storage.  Suggested steps:
        1. Return immediately if _is_uncacheable(query)
        2. Build key: f"{self.prefix}{self._query_hash(query)}"
        3. self._redis.hset(key, mapping={"query": query, "response": value})
        4. self._redis.expire(key, self.ttl_seconds)
        """
        if _is_uncacheable(query):
            return

        key = f"{self.prefix}{self._query_hash(query)}"
        self._redis.hset(
            key,
            mapping={
                "query": query,
                "response": value,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        self._redis.expire(key, self.ttl_seconds)

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]

    @staticmethod
    def _decode_metadata(entry: dict[str, Any]) -> dict[str, str]:
        raw = entry.get("metadata")
        if raw is None:
            return {}
        try:
            decoded = json.loads(str(raw))
        except json.JSONDecodeError:
            return {}
        if not isinstance(decoded, dict):
            return {}
        return {str(key): str(value) for key, value in decoded.items()}
