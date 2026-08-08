"""Vector-RAG baseline: chunker, embedding seam, index, retrieval (design §9, ADR-0005).

The RAG condition answers over the *identical* normalized corpus as PD — the
reference snapshots of the current revision — chunked deterministically and
retrieved by cosine similarity behind a vector-adapter interface. okf-core owns
only the network-free parts: the chunker, the index format, the retrieval math,
and a deterministic stub embedder for CI. Real embedding providers live in
``oknoll_providers`` (okf-core never reaches the network).

The vector index is derived state under ``.oknoll/rag-index/``, keyed by
revision id + embedder id — rebuildable and replaceable, never bundle content
(design §9.4).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from okf_core import bundle as bundle_mod
from okf_core.canonical import sha256_hex
from okf_core.frontmatter import parse_document
from okf_core.indexing import tokenize
from okf_core.revision import read_current_revision_id

# One chunk == one ask evidence excerpt (okf_core.ask.EXCERPT_CHARS,
# contract-tested), so PD and RAG run on equal per-passage evidence budgets.
CHUNK_CHARS = 500
STUB_EMBED_DIM = 256
RAG_INDEX_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/rag-index"
RAG_INDEX_SCHEMA_VERSION = 1


class EmbeddingProvider(Protocol):
    """A bounded text-embedding interface; retrieval never calls models directly."""

    id: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, all of equal dimension."""
        ...


class StubEmbeddingProvider:
    """Deterministic CI embedder: L2-normalized hashed bag-of-words.

    A pure function of the text — no network, no weights — so the whole RAG
    condition is reproducible in CI. Shared tokens produce positive cosine
    similarity, which is all retrieval tests and the stub eval run need.
    """

    id = "stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hashed_bow(text) for text in texts]


def _hashed_bow(text: str) -> list[float]:
    vector = [0.0] * STUB_EMBED_DIM
    for token in sorted(tokenize(text)):
        vector[int(sha256_hex(token)[:8], 16) % STUB_EMBED_DIM] += 1.0
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector] if norm else vector


def resolve_embedder(name: str) -> EmbeddingProvider:
    """Resolve a configured embedder name (available here: stub).

    Real embedders (``ollama:<model>``) resolve via ``oknoll_providers`` —
    okf-core stays network-free.
    """
    if name == "stub":
        return StubEmbeddingProvider()
    raise ValueError(
        f"unknown embedding provider {name!r} — available: 'stub' "
        "(real embedders resolve via oknoll_providers)"
    )


@dataclass(frozen=True, slots=True)
class Chunk:
    path: str  # bundle-relative reference path
    seq: int  # chunk ordinal within that file
    text: str


def chunk_references(bundle_dir: Path) -> list[Chunk]:
    """Deterministic chunks over every reference snapshot body.

    Paragraph-aligned packing: consecutive paragraphs are packed into chunks of
    at most ``CHUNK_CHARS``; a single oversized paragraph is hard-split. File
    order is sorted, chunk order is document order — byte-stable across runs.
    """
    chunks: list[Chunk] = []
    refs_dir = bundle_dir / bundle_mod.REFERENCES_DIR
    if not refs_dir.is_dir():
        return chunks
    for file_path in sorted(refs_dir.glob("*.md")):
        body = parse_document(file_path.read_text(encoding="utf-8")).body
        rel_path = f"{bundle_mod.REFERENCES_DIR}/{file_path.name}"
        seq = 0
        buffer = ""
        for paragraph in (p.strip() for p in body.split("\n\n")):
            if not paragraph:
                continue
            pieces = [paragraph[i : i + CHUNK_CHARS] for i in range(0, len(paragraph), CHUNK_CHARS)]
            for piece in pieces:
                if buffer and len(buffer) + 1 + len(piece) > CHUNK_CHARS:
                    chunks.append(Chunk(path=rel_path, seq=seq, text=buffer))
                    seq += 1
                    buffer = ""
                buffer = piece if not buffer else f"{buffer}\n{piece}"
        if buffer:
            chunks.append(Chunk(path=rel_path, seq=seq, text=buffer))
    return chunks


def _index_path(bundle_dir: Path, revision_id: str, embedder_id: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", embedder_id.lower()).strip("-")
    return bundle_dir / RAG_INDEX_DIR / f"{revision_id}--{slug}.json"


def ensure_rag_index(bundle_dir: Path, embedder: EmbeddingProvider) -> dict[str, Any]:
    """Load the cached vector index for the current revision, building it if absent.

    Foreign/hand-authored bundles without a published revision are indexed in
    memory only — a disk cache without a revision id to key on could go stale.
    """
    revision_id = read_current_revision_id(bundle_dir)

    index_path = (
        _index_path(bundle_dir, revision_id, embedder.id) if revision_id is not None else None
    )
    if index_path is not None and index_path.is_file():
        cached = json.loads(index_path.read_text(encoding="utf-8"))
        if (
            cached.get("schema_version") == RAG_INDEX_SCHEMA_VERSION
            and cached.get("revision_id") == revision_id
            and cached.get("embedder") == embedder.id
        ):
            return dict(cached)

    chunks = chunk_references(bundle_dir)
    vectors = embedder.embed([chunk.text for chunk in chunks]) if chunks else []
    index: dict[str, Any] = {
        "schema_version": RAG_INDEX_SCHEMA_VERSION,
        "revision_id": revision_id,
        "embedder": embedder.id,
        "chunks": [
            {"path": chunk.path, "seq": chunk.seq, "text": chunk.text, "vector": vector}
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    }
    if index_path is not None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def retrieve(index: dict[str, Any], query_vector: list[float], k: int) -> list[dict[str, Any]]:
    """Top-k chunks by cosine similarity; deterministic ties by (path, seq).

    Vectors are stored L2-normalized (stub) or normalized on ingest by real
    providers' adapters, so the dot product is the cosine. Zero-or-negative
    similarity never qualifies as evidence.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in index["chunks"]:
        score = sum(a * b for a, b in zip(query_vector, chunk["vector"], strict=False))
        if score > 0.0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["seq"]))
    return [
        {"path": chunk["path"], "seq": chunk["seq"], "text": chunk["text"], "score": score}
        for score, chunk in scored[:k]
    ]
