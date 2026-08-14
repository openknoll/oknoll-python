"""OkNoll image spec v1: OCI media types and the image-config contract.

An OkNoll image is a plain OCI image manifest (artifact semantics) whose single
layer is the exact byte-deterministic ``pack_bundle`` archive and whose config
is the small JSON blob validated here. The layer digest is over the compressed
bytes; the config's ``bundleRevision`` (``rev-…``) carries semantic identity —
after ``@`` in any locator, ``sha256:`` always means the OCI manifest digest.

Producer discipline is strict (``validate_image_config`` on everything we
emit); consumption stays permissive — unknown config fields are preserved and
never rejected. The normative prose spec lives in ``spec/image-config-v1.md``
at the repo root; ``IMAGE_CONFIG_SCHEMA`` is the same contract as machine data.
"""

from __future__ import annotations

import re
from typing import Any

ARTIFACT_TYPE = "application/vnd.openknoll.okf.bundle.v1"
CONFIG_MEDIA_TYPE = "application/vnd.openknoll.okf.config.v1+json"
LAYER_MEDIA_TYPE = "application/vnd.openknoll.okf.bundle.v1.tar+gzip"

IMAGE_CONFIG_SCHEMA_VERSION = 1

# Capabilities a v1 image may declare. Unknown capabilities in foreign images
# are preserved but flagged by validation of *our own* output only.
KNOWN_CAPABILITIES: tuple[str, ...] = ("explore", "ask")

REVISION_ID_PATTERN = r"^rev-[0-9a-f]{12}$"
_VERSION_PATTERN = r"^[0-9]+\.[0-9]+(\.[0-9]+)?([a-z0-9.+-]*)$"

# JSON Schema (draft 2020-12) for the v1 image config. `additionalProperties`
# stays true: permissive consumer — foreign configs may carry fields we do not
# know, and installers must keep them intact.
IMAGE_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://openknoll.com/schemas/okf-image-config.v1.json",
    "title": "OkNoll image config v1",
    "type": "object",
    "required": ["schemaVersion", "okfVersion", "title", "bundleRevision"],
    "additionalProperties": True,
    "properties": {
        "schemaVersion": {"const": IMAGE_CONFIG_SCHEMA_VERSION},
        "okfVersion": {"type": "string", "pattern": _VERSION_PATTERN},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string", "maxLength": 2000},
        "bundleRevision": {"type": "string", "pattern": REVISION_ID_PATTERN},
        "minimumOknollVersion": {"type": "string", "pattern": _VERSION_PATTERN},
        "capabilities": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    },
}


def validate_image_config(config: object) -> list[str]:
    """Validate a config against the v1 contract; return error strings.

    Empty list means valid. Checks mirror ``IMAGE_CONFIG_SCHEMA`` exactly
    (kept hand-rolled so okf-core gains no dependency); unknown extra fields
    are never an error.
    """
    if not isinstance(config, dict):
        return ["config must be a JSON object"]

    errors: list[str] = []

    def _string(key: str, *, required: bool, pattern: str | None = None, max_len: int = 0) -> None:
        value = config.get(key)
        if value is None:
            if required:
                errors.append(f"{key}: required field missing")
            return
        if not isinstance(value, str):
            errors.append(f"{key}: must be a string")
            return
        if required and not value:
            errors.append(f"{key}: must not be empty")
            return
        if pattern is not None and value and not re.match(pattern, value):
            errors.append(f"{key}: {value!r} does not match {pattern}")
        if max_len and len(value) > max_len:
            errors.append(f"{key}: longer than {max_len} characters")

    if config.get("schemaVersion") != IMAGE_CONFIG_SCHEMA_VERSION:
        errors.append(
            f"schemaVersion: must be {IMAGE_CONFIG_SCHEMA_VERSION}, "
            f"got {config.get('schemaVersion')!r}"
        )
    _string("okfVersion", required=True, pattern=_VERSION_PATTERN)
    _string("title", required=True, max_len=200)
    _string("description", required=False, max_len=2000)
    _string("bundleRevision", required=True, pattern=REVISION_ID_PATTERN)
    _string("minimumOknollVersion", required=False, pattern=_VERSION_PATTERN)

    capabilities = config.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item for item in capabilities
        ):
            errors.append("capabilities: must be an array of non-empty strings")
        elif len(set(capabilities)) != len(capabilities):
            errors.append("capabilities: entries must be unique")

    return errors
