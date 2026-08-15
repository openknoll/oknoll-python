"""Unified bundle locator parser — spec/locator-grammar.md v1.

One grammar spans unmanaged paths, installed images (``local:``), OCI
registries (``oci://``), and OpenKnoll-compatible hosts (``oknoll://``). The
committed vectors in fixtures/locators/vectors.json are the contract; the
parser must pass every case verbatim. Two rules do the security work: after
``@`` only ``sha256:`` + 64 lowercase hex is a digest (always the OCI manifest
digest — ``rev-…`` and bare hashes are parse errors), and bare names are an
interactive convenience that machine contexts must reject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ALL_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_LOWER_HEX_RE = re.compile(r"^[0-9a-f]+$")
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$"
)


class LocatorError(ValueError):
    """A rejected locator. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Locator:
    kind: str  # path | local | oci | oknoll | bare
    path: str | None = None
    name: str | None = None
    host: str | None = None
    repository: str | None = None
    namespace: str | None = None
    tag: str | None = None
    digest: str | None = None

    def to_dict(self) -> dict[str, str]:
        """The vector-fixture shape: kind plus every populated component."""
        fields = ("path", "name", "host", "repository", "namespace", "tag", "digest")
        result = {"kind": self.kind}
        for field in fields:
            value: str | None = getattr(self, field)
            if value is not None:
                result[field] = value
        return result


def parse_locator(text: str) -> Locator:
    """Parse any locator; bare names come back as kind ``bare``."""
    if not text:
        raise LocatorError("empty_locator", "empty locator")
    if text.startswith(("/", "./", "../", "~/")) or text in (".", ".."):
        return Locator(kind="path", path=text)

    scheme_match = _SCHEME_RE.match(text)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        rest = text[scheme_match.end() :]
        if scheme == "file":
            if not rest:
                raise LocatorError("empty_locator", "file: locator has no path")
            return Locator(kind="path", path=rest)
        if scheme == "local":
            return _parse_local(rest)
        if scheme == "cloud":
            raise LocatorError(
                "superseded_scheme", "cloud:<bundle-id> is superseded — use oknoll://<host>/…"
            )
        if scheme in ("oci", "oknoll"):
            if not rest.startswith("//"):
                raise LocatorError("malformed_locator", f"{scheme}: locators use {scheme}://…")
            return _parse_remote(scheme, rest[2:])
        raise LocatorError("unknown_scheme", f"unknown locator scheme {scheme!r}")

    if "/" in text:
        return Locator(kind="path", path=text)
    if ALIAS_RE.match(text):
        return Locator(kind="bare", name=text)
    raise LocatorError(
        "invalid_bare_name",
        f"{text!r} is not a valid alias (aliases match [a-z0-9][a-z0-9._-]*)",
    )


def parse_machine_locator(text: str) -> Locator:
    """Parse for machine contexts: bare names must carry an explicit scheme."""
    locator = parse_locator(text)
    if locator.kind == "bare":
        raise LocatorError(
            "bare_name_not_allowed",
            f"bare name {text!r} needs an explicit scheme here (try local:{text})",
        )
    return locator


def _parse_local(rest: str) -> Locator:
    if "/" in rest:
        raise LocatorError("alias_contains_slash", f"catalog aliases contain no '/': {rest!r}")
    alias, _, tag = rest.partition(":")
    if not ALIAS_RE.match(alias):
        raise LocatorError(
            "invalid_alias", f"{alias!r} is not a valid alias ([a-z0-9][a-z0-9._-]*)"
        )
    return Locator(
        kind="local", name=alias, tag=_validated_tag(tag) if tag or ":" in rest else None
    )


def _parse_remote(scheme: str, rest: str) -> Locator:
    if rest.endswith("/"):
        raise LocatorError("trailing_slash", f"trailing slash in locator: {rest!r}")

    digest: str | None = None
    if "@" in rest:
        rest, _, digest_part = rest.partition("@")
        digest = digest_part  # validated after the tag check below
    host, _, path = rest.partition("/")
    if not path:
        if scheme == "oknoll":
            raise LocatorError("missing_namespace", "oknoll:// locators need <namespace>/<name>")
        raise LocatorError("missing_repository", "oci:// locators need a repository path")

    host = host.lower()
    if not _HOST_RE.match(host):
        raise LocatorError("invalid_host", f"invalid host {host!r}")

    segments = path.split("/")
    last, _, tag = segments[-1].partition(":")
    segments[-1] = last
    if tag and digest is not None:
        raise LocatorError(
            "tag_and_digest", "tag and digest are mutually exclusive — give one, not both"
        )
    if digest is not None:
        digest = _validated_digest(digest)
    validated_tag = _validated_tag(tag) if ":" in path.rsplit("/", 1)[-1] else None

    if scheme == "oknoll":
        if len(segments) == 1:
            raise LocatorError("missing_namespace", "oknoll:// locators need <namespace>/<name>")
        if len(segments) > 2:
            raise LocatorError(
                "extra_path_segments", "oknoll:// locators take exactly <namespace>/<name>"
            )
        namespace, name = segments
        for part in (namespace, name):
            if not ALIAS_RE.match(part):
                raise LocatorError("invalid_alias", f"invalid path segment {part!r}")
        return Locator(
            kind="oknoll",
            host=host,
            namespace=namespace,
            name=name,
            tag=validated_tag,
            digest=digest,
        )

    for part in segments:
        if not ALIAS_RE.match(part):
            raise LocatorError("invalid_repository", f"invalid repository segment {part!r}")
    return Locator(
        kind="oci",
        host=host,
        repository="/".join(segments),
        tag=validated_tag,
        digest=digest,
    )


def _validated_tag(tag: str) -> str:
    if not tag:
        raise LocatorError("empty_tag", "empty tag after ':'")
    if not TAG_RE.match(tag):
        raise LocatorError("invalid_tag", f"invalid tag {tag!r}")
    return tag


def _validated_digest(value: str) -> str:
    if value.startswith("rev-"):
        raise LocatorError(
            "revision_not_a_locator_digest",
            "OKF revisions (rev-…) are never a locator digest — after '@', "
            "sha256: always means the OCI manifest digest",
        )
    if _ALL_HEX_RE.match(value):
        raise LocatorError(
            "bare_hash", "bare hashes are ambiguous — spell the digest sha256:<64-hex>"
        )
    algorithm, sep, hex_part = value.partition(":")
    if not sep:
        raise LocatorError("invalid_digest", f"invalid digest {value!r}")
    if algorithm != "sha256":
        raise LocatorError(
            "unsupported_digest_algorithm", f"unsupported digest algorithm {algorithm!r}"
        )
    if not _LOWER_HEX_RE.match(hex_part):
        if _ALL_HEX_RE.match(hex_part):
            raise LocatorError("digest_not_lowercase_hex", "digest hex must be lowercase")
        raise LocatorError("invalid_digest", f"invalid digest hex {hex_part!r}")
    if len(hex_part) != 64:
        raise LocatorError("digest_wrong_length", "sha256 digests are exactly 64 hex characters")
    return value
