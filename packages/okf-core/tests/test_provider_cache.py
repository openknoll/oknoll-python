"""Model provider stub determinism and build-cache behavior."""

from pathlib import Path

import pytest
from okf_core import (
    BuildCache,
    StubModelProvider,
    generation_cache_key,
    generation_timestamp_key,
    resolve_provider,
)


def test_stub_is_deterministic() -> None:
    provider = StubModelProvider()
    payload = {"title": "Security policy", "excerpt": "Access is deny by default."}
    assert provider.complete("concept-description", payload) == provider.complete(
        "concept-description", dict(payload)
    )


def test_stub_rejects_unknown_prompt() -> None:
    with pytest.raises(ValueError, match="no prompt"):
        StubModelProvider().complete("nonexistent-prompt", {})


def test_resolve_provider() -> None:
    assert resolve_provider("stub").id == "stub"
    with pytest.raises(ValueError, match="unknown model provider"):
        resolve_provider("gpt-42")


def test_cache_key_varies_with_every_input() -> None:
    base = {
        "content_hash": "abc",
        "prompt_id": "concept-description",
        "provider_id": "stub",
    }
    key = generation_cache_key(**base)
    assert key == generation_cache_key(**base)
    assert key != generation_cache_key(**{**base, "content_hash": "abd"})
    assert key != generation_cache_key(**{**base, "provider_id": "real"})
    assert key != generation_cache_key(**base, prompt_version="99")
    assert key != generation_cache_key(**base, generator_version="99")
    assert key != generation_cache_key(**base, generation_version="99")


def test_timestamp_key_shared_across_generation_versions() -> None:
    base = {
        "content_hash": "abc",
        "prompt_id": "concept-description",
        "provider_id": "stub",
    }
    key = generation_timestamp_key(**base)
    assert key == generation_timestamp_key(**base)
    assert key != generation_timestamp_key(**{**base, "content_hash": "abd"})
    assert key != generation_timestamp_key(**{**base, "provider_id": "real"})
    assert key != generation_timestamp_key(**base, prompt_version="99")
    assert key != generation_timestamp_key(**base, generator_version="99")
    # The whole point: cache keys diverge across a generation_version bump,
    # the timestamp identity does not.
    assert generation_cache_key(**base) != generation_cache_key(**base, generation_version="1")


def test_stable_generated_at_follows_the_output(tmp_path: Path) -> None:
    path = tmp_path / "build-cache.json"
    cache = BuildCache.load(path)
    # Identical output keeps its first-produced timestamp, also after reload.
    assert cache.stable_generated_at("ts-key", "fp-1", "2026-08-01T00:00:00Z") == (
        "2026-08-01T00:00:00Z"
    )
    cache.save()
    reloaded = BuildCache.load(path)
    assert reloaded.stable_generated_at("ts-key", "fp-1", "2027-01-01T00:00:00Z") == (
        "2026-08-01T00:00:00Z"
    )
    # Changed output re-mints — inherited timestamps would lie about when the
    # new output was produced.
    assert reloaded.stable_generated_at("ts-key", "fp-2", "2027-01-01T00:00:00Z") == (
        "2027-01-01T00:00:00Z"
    )
    # And it does not touch the hit/miss counters.
    assert reloaded.hits == 0 and reloaded.misses == 0


def test_build_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "build-cache.json"
    cache = BuildCache.load(path)
    assert cache.acquired_at("hash-1", "2026-08-01T00:00:00Z") == "2026-08-01T00:00:00Z"
    cache.store_generated("key-1", {"description": "d", "generated_at": "2026-08-01T00:00:00Z"})
    cache.save()

    reloaded = BuildCache.load(path)
    # Later retrieval times never overwrite the first-seen provenance.
    assert reloaded.acquired_at("hash-1", "2027-01-01T00:00:00Z") == "2026-08-01T00:00:00Z"
    assert reloaded.generated("key-1") == {
        "description": "d",
        "generated_at": "2026-08-01T00:00:00Z",
    }
    assert reloaded.hits == 2


def test_corrupt_cache_is_discarded(tmp_path: Path) -> None:
    path = tmp_path / "build-cache.json"
    path.write_text("{ not json", encoding="utf-8")
    cache = BuildCache.load(path)
    assert cache.acquire == {} and cache.generate == {}
