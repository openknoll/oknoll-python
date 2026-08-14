"""Image spec v1: media types, config schema, strict-producer validation."""

from typing import Any

from okf_core.image_spec import (
    ARTIFACT_TYPE,
    CONFIG_MEDIA_TYPE,
    IMAGE_CONFIG_SCHEMA,
    IMAGE_CONFIG_SCHEMA_VERSION,
    LAYER_MEDIA_TYPE,
    validate_image_config,
)


def _valid_config() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "okfVersion": "0.2",
        "title": "Acme Engineering Handbook",
        "description": "Engineering policy and operational knowledge",
        "bundleRevision": "rev-a4d583b7229c",
        "minimumOknollVersion": "0.4.0",
        "capabilities": ["explore", "ask"],
    }


def test_media_types_are_frozen() -> None:
    assert ARTIFACT_TYPE == "application/vnd.openknoll.okf.bundle.v1"
    assert CONFIG_MEDIA_TYPE == "application/vnd.openknoll.okf.config.v1+json"
    assert LAYER_MEDIA_TYPE == "application/vnd.openknoll.okf.bundle.v1.tar+gzip"


def test_schema_is_versioned_and_permissive() -> None:
    assert IMAGE_CONFIG_SCHEMA_VERSION == 1
    assert IMAGE_CONFIG_SCHEMA["properties"]["schemaVersion"]["const"] == 1
    # Permissive consumer: unknown fields must never be schema-rejected.
    assert IMAGE_CONFIG_SCHEMA["additionalProperties"] is True
    assert sorted(IMAGE_CONFIG_SCHEMA["required"]) == [
        "bundleRevision",
        "okfVersion",
        "schemaVersion",
        "title",
    ]


def test_valid_config_passes() -> None:
    assert validate_image_config(_valid_config()) == []


def test_minimal_config_passes() -> None:
    config = {
        "schemaVersion": 1,
        "okfVersion": "0.2",
        "title": "T",
        "bundleRevision": "rev-000000000000",
    }
    assert validate_image_config(config) == []


def test_unknown_extra_fields_are_not_errors() -> None:
    config = _valid_config()
    config["x-foreign-field"] = {"anything": ["at", "all"]}
    assert validate_image_config(config) == []


def test_missing_required_fields_reported_individually() -> None:
    errors = validate_image_config({})
    joined = "\n".join(errors)
    for key in ("schemaVersion", "okfVersion", "title", "bundleRevision"):
        assert key in joined
    assert len(errors) == 4


def test_wrong_schema_version_rejected() -> None:
    config = _valid_config()
    config["schemaVersion"] = 2
    assert any("schemaVersion" in error for error in validate_image_config(config))


def test_malformed_revision_rejected() -> None:
    for bad in ("a4d583b7229c", "sha256:ab34", "rev-XYZ", "rev-a4d583b7229c00"):
        config = _valid_config()
        config["bundleRevision"] = bad
        assert any("bundleRevision" in e for e in validate_image_config(config)), bad


def test_wrong_types_rejected() -> None:
    config = _valid_config()
    config["title"] = 7
    config["capabilities"] = "explore"
    errors = validate_image_config(config)
    assert any("title" in error for error in errors)
    assert any("capabilities" in error for error in errors)


def test_duplicate_capabilities_rejected() -> None:
    config = _valid_config()
    config["capabilities"] = ["explore", "explore"]
    assert any("unique" in error for error in validate_image_config(config))


def test_non_object_config_rejected() -> None:
    assert validate_image_config([1, 2]) == ["config must be a JSON object"]
    assert validate_image_config("{}") == ["config must be a JSON object"]
