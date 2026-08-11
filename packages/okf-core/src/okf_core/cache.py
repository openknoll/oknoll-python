"""Build cache: the piece of derived state that makes rebuilds byte-reproducible.

Three sections, all keyed by content:

- ``acquire``: per source_hash, the provenance recorded on first acquisition
  (``retrieved_at``). Reusing it keeps reference snapshots byte-stable when the
  source bytes have not changed.
- ``generate``: per generation_cache_key, the model-generated fields and the
  ``generated.at`` timestamp minted when they were first produced.
- ``generated_at``: per generation_timestamp_key (the cache key minus
  ``generation_version``), the first-produced timestamp plus a fingerprint of
  the output it belongs to. A deliberate regeneration that reproduces
  identical output keeps its timestamp — so a ``generation_version`` bump
  under a deterministic model republishes nothing; changed output re-mints it.

Lives under ``bundle/.oknoll/cache/`` — derived state, never packed. Losing it
costs reproducibility of timestamps, never knowledge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CACHE_VERSION = 1


@dataclass
class BuildCache:
    path: Path
    acquire: dict[str, dict[str, Any]] = field(default_factory=dict)
    generate: dict[str, dict[str, Any]] = field(default_factory=dict)
    generated_at: dict[str, dict[str, Any]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @classmethod
    def load(cls, path: Path) -> BuildCache:
        cache = cls(path=path)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return cache  # a corrupt cache only costs regeneration
            if isinstance(data, dict) and data.get("version") == _CACHE_VERSION:
                acquire = data.get("acquire")
                generate = data.get("generate")
                generated_at = data.get("generated_at")
                if isinstance(acquire, dict):
                    cache.acquire = acquire
                if isinstance(generate, dict):
                    cache.generate = generate
                if isinstance(generated_at, dict):
                    cache.generated_at = generated_at
        return cache

    def save(self) -> None:
        payload = {
            "version": _CACHE_VERSION,
            "acquire": self.acquire,
            "generate": self.generate,
            "generated_at": self.generated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def acquired_at(self, source_hash: str, retrieved_at: str) -> str:
        """First-seen retrieval time for these bytes; stable on later builds."""
        entry = self.acquire.get(source_hash)
        if entry is not None and isinstance(entry.get("retrieved_at"), str):
            self.hits += 1
            return str(entry["retrieved_at"])
        self.misses += 1
        self.acquire[source_hash] = {"retrieved_at": retrieved_at}
        return retrieved_at

    def generated(self, key: str) -> dict[str, Any] | None:
        entry = self.generate.get(key)
        if entry is not None:
            self.hits += 1
        return entry

    def store_generated(self, key: str, fields: dict[str, Any]) -> None:
        self.misses += 1
        self.generate[key] = fields

    def stable_generated_at(self, timestamp_key: str, fingerprint: str, minted: str) -> str:
        """First time this exact output was produced; re-minted when it changes.

        Rides along with a ``store_generated`` miss, so it keeps its own
        entries out of the hit/miss counters. The fingerprint guard keeps
        provenance honest: a regeneration whose output differs gets a fresh
        timestamp instead of inheriting the old one.
        """
        entry = self.generated_at.get(timestamp_key)
        if (
            entry is not None
            and entry.get("fingerprint") == fingerprint
            and isinstance(entry.get("at"), str)
        ):
            return str(entry["at"])
        self.generated_at[timestamp_key] = {"at": minted, "fingerprint": fingerprint}
        return minted
