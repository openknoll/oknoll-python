"""okf-core: CanonicalDoc, OKF v0.2 parser/writer, five-level lint, pipeline,
revisions, packer.

The heart of OpenKnoll — the CLI and the cloud pipeline job run this identical
code.
"""

from okf_core._version import __version__
from okf_core.ask import AskResult, Citation, answer_question, write_trace
from okf_core.cache import BuildCache
from okf_core.canonical import (
    Anchor,
    Block,
    CanonicalDoc,
    Link,
    MediaRef,
    SourceRef,
    sha256_hex,
)
from okf_core.explorer import Explorer, ExplorerError
from okf_core.findings import Finding, Level, LintReport, Severity
from okf_core.frontmatter import (
    KNOWN_KEY_ORDER,
    STATUS_VALUES,
    Frontmatter,
    FrontmatterError,
    FrontmatterShapeError,
    FrontmatterYamlError,
    ParsedDocument,
    parse_document,
    write_document,
)
from okf_core.image_spec import (
    ARTIFACT_TYPE,
    CONFIG_MEDIA_TYPE,
    IMAGE_CONFIG_SCHEMA,
    IMAGE_CONFIG_SCHEMA_VERSION,
    LAYER_MEDIA_TYPE,
    validate_image_config,
)
from okf_core.indexing import build_link_graph, ensure_index, search_index, write_index
from okf_core.lint import LintConfig, lint_bundle
from okf_core.packer import PackResult, pack_bundle, strip_okf_fields
from okf_core.pipeline import (
    BuildOutcome,
    PipelineError,
    PipelineSource,
    build_revision,
    check_reproducibility,
)
from okf_core.provider import (
    DEFAULT_GENERATION_VERSION,
    GENERATOR_VERSION,
    ModelProvider,
    StubModelProvider,
    generation_cache_key,
    generation_timestamp_key,
    resolve_provider,
)
from okf_core.rag import EmbeddingProvider, StubEmbeddingProvider, resolve_embedder
from okf_core.revdiff import RevisionDiffError, diff_revisions
from okf_core.revision import read_current_revision_id, revision_dir
from okf_core.viz import build_graph, render_html, write_viz

__all__ = [
    "ARTIFACT_TYPE",
    "CONFIG_MEDIA_TYPE",
    "DEFAULT_GENERATION_VERSION",
    "GENERATOR_VERSION",
    "IMAGE_CONFIG_SCHEMA",
    "IMAGE_CONFIG_SCHEMA_VERSION",
    "KNOWN_KEY_ORDER",
    "LAYER_MEDIA_TYPE",
    "STATUS_VALUES",
    "Anchor",
    "AskResult",
    "Block",
    "BuildCache",
    "BuildOutcome",
    "CanonicalDoc",
    "Citation",
    "EmbeddingProvider",
    "Explorer",
    "ExplorerError",
    "Finding",
    "Frontmatter",
    "FrontmatterError",
    "FrontmatterShapeError",
    "FrontmatterYamlError",
    "Level",
    "Link",
    "LintConfig",
    "LintReport",
    "MediaRef",
    "ModelProvider",
    "PackResult",
    "ParsedDocument",
    "PipelineError",
    "PipelineSource",
    "RevisionDiffError",
    "Severity",
    "SourceRef",
    "StubEmbeddingProvider",
    "StubModelProvider",
    "__version__",
    "answer_question",
    "build_graph",
    "build_link_graph",
    "build_revision",
    "check_reproducibility",
    "diff_revisions",
    "ensure_index",
    "generation_cache_key",
    "generation_timestamp_key",
    "lint_bundle",
    "pack_bundle",
    "parse_document",
    "read_current_revision_id",
    "render_html",
    "resolve_embedder",
    "resolve_provider",
    "revision_dir",
    "search_index",
    "sha256_hex",
    "strip_okf_fields",
    "validate_image_config",
    "write_document",
    "write_index",
    "write_trace",
    "write_viz",
]
