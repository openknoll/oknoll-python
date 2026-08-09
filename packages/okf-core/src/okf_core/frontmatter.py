"""OKF v0.2 frontmatter: permissive parser, strict deterministic writer.

Parser posture: tolerate unknown keys and unknown values; keep every field we do
not understand. Timestamps and dates are kept as strings so a round trip never
reformats them. Writer posture: canonical key order for known fields, then
`openknoll_*` extensions, then unknown fields in their original relative order;
same input always produces identical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

# Canonical emission order for known OKF v0.2 keys.
KNOWN_KEY_ORDER: tuple[str, ...] = (
    "okf_version",
    "type",
    "title",
    "description",
    "tags",
    "status",
    "generated",
    "verified",
    "sources",
    "stale_after",
)

STATUS_VALUES: tuple[str, ...] = ("draft", "stable", "deprecated")

_DELIMITER = "---"


class FrontmatterError(ValueError):
    """Base error for frontmatter problems."""


class FrontmatterYamlError(FrontmatterError):
    """The frontmatter block is not parseable YAML (or is unterminated)."""


class FrontmatterShapeError(FrontmatterError):
    """The frontmatter parsed but is not a mapping."""


class _PermissiveLoader(yaml.SafeLoader):
    """SafeLoader minus timestamp resolution: date-like strings stay strings."""


_PermissiveLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class _DeterministicDumper(yaml.SafeDumper):
    """Block-style, insertion-ordered, no aliases — stable bytes for stable input."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """A parsed frontmatter mapping. `data` preserves source key order verbatim."""

    data: dict[str, Any]

    def _str(self, key: str) -> str | None:
        value = self.data.get(key)
        return value if isinstance(value, str) else None

    @property
    def type(self) -> str | None:
        return self._str("type")

    @property
    def title(self) -> str | None:
        return self._str("title")

    @property
    def description(self) -> str | None:
        return self._str("description")

    @property
    def status(self) -> str | None:
        return self._str("status")

    @property
    def stale_after(self) -> str | None:
        return self._str("stale_after")

    @property
    def okf_version(self) -> str | None:
        return self._str("okf_version")

    @property
    def tags(self) -> list[str]:
        value = self.data.get("tags")
        if isinstance(value, list):
            return [t for t in value if isinstance(t, str)]
        return []

    @property
    def generated(self) -> dict[str, Any] | None:
        value = self.data.get("generated")
        return value if isinstance(value, dict) else None

    @property
    def verified(self) -> list[dict[str, Any]]:
        value = self.data.get("verified")
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        return []

    @property
    def sources(self) -> list[dict[str, Any]]:
        value = self.data.get("sources")
        if isinstance(value, list):
            return [s for s in value if isinstance(s, dict)]
        return []

    def source_ids(self) -> list[str]:
        return [s["id"] for s in self.sources if isinstance(s.get("id"), str)]

    def unknown_keys(self) -> tuple[str, ...]:
        return tuple(
            k for k in self.data if k not in KNOWN_KEY_ORDER and not k.startswith("openknoll_")
        )


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """A Markdown file split into optional frontmatter and body."""

    frontmatter: Frontmatter | None
    body: str


def parse_document(text: str) -> ParsedDocument:
    """Split and parse a Markdown document.

    Raises FrontmatterYamlError / FrontmatterShapeError; a document without a
    leading `---` block is valid and returns frontmatter=None.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _DELIMITER:
        return ParsedDocument(frontmatter=None, body=text)

    for i, line in enumerate(lines[1:], start=1):
        if line.strip() in (_DELIMITER, "..."):
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            try:
                data = yaml.load(raw, Loader=_PermissiveLoader)
            except yaml.YAMLError as exc:
                raise FrontmatterYamlError(f"invalid YAML frontmatter: {exc}") from exc
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise FrontmatterShapeError(
                    f"frontmatter must be a mapping, got {type(data).__name__}"
                )
            return ParsedDocument(frontmatter=Frontmatter(data=data), body=body)

    raise FrontmatterYamlError("unterminated frontmatter block (missing closing '---')")


def canonical_order(data: dict[str, Any]) -> dict[str, Any]:
    """Known keys in canonical order, then openknoll_* extensions, then unknown keys."""
    known = {k: data[k] for k in KNOWN_KEY_ORDER if k in data}
    extensions = {k: v for k, v in data.items() if k.startswith("openknoll_") and k not in known}
    unknown = {k: v for k, v in data.items() if k not in known and k not in extensions}
    return {**known, **extensions, **unknown}


def write_document(doc: ParsedDocument) -> str:
    """Serialize deterministically: canonical key order, block YAML, one trailing newline."""
    body = doc.body.strip("\n")
    if doc.frontmatter is None or not doc.frontmatter.data:
        return body + "\n" if body else ""

    rendered = yaml.dump(
        canonical_order(doc.frontmatter.data),
        Dumper=_DeterministicDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )
    head = f"{_DELIMITER}\n{rendered}{_DELIMITER}\n"
    if not body:
        return head
    return f"{head}\n{body}\n"
