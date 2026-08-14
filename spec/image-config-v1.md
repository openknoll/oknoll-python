# OkNoll image spec v1 — media types and image config

Status: **stable** (v1 — additive changes only; a breaking change means a new
`schemaVersion` and new media-type names)
Code: `okf_core.image_spec` (constants, JSON Schema, validation)

An **OkNoll image** is a standard OCI *image manifest* using artifact semantics
(OCI Image Spec 1.1) whose payload is exactly one OKF bundle. It is data, not a
container: any ORAS-compatible registry or client can transport it without
understanding OKF.

## Media types

```text
artifactType:        application/vnd.openknoll.okf.bundle.v1
config media type:   application/vnd.openknoll.okf.config.v1+json
layer media type:    application/vnd.openknoll.okf.bundle.v1.tar+gzip
```

## Image contents

1. **One config blob** (`application/vnd.openknoll.okf.config.v1+json`) — the
   small deterministic JSON object specified below.
2. **One layer** — the exact byte-deterministic `pack_bundle` tar.gz of the
   bundle (sorted members, zeroed timestamps/ownership, normalized modes, gzip
   `mtime=0`). The canonical archive filename extension is `.okf.tgz`;
   `.tar.gz` is accepted on input forever. The OCI layer digest is over the
   **compressed** bytes — archive determinism is therefore contractual.
3. **Standard OCI annotations** for name, description, version, authorship,
   creation tool, and documentation/license/source URLs where applicable.

Images never contain `.oknoll/` local state, credentials or tokens,
conversations or traces, machine paths, vector indexes, or executable hooks.

## Identity

An image carries three identities that must never be conflated:

```text
OKF revision       rev-a4d583b7229c     semantic bundle content (config field)
OCI manifest       sha256:ab348f…       the distribution artifact
Human reference    registry/name:tag    mutable pointer
```

After `@` in any locator or citation, `sha256:` **always** means the OCI
manifest digest. OKF revisions are only ever spelled `rev-…`. Bare hashes are
invalid everywhere.

## Image config v1

Example:

```json
{
  "schemaVersion": 1,
  "okfVersion": "0.2",
  "title": "Acme Engineering Handbook",
  "description": "Engineering policy and operational knowledge",
  "bundleRevision": "rev-a4d583b7229c",
  "minimumOknollVersion": "0.4.0",
  "capabilities": ["explore", "ask"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schemaVersion` | yes | Literal `1` for this spec. |
| `okfVersion` | yes | OKF format version of the payload bundle (e.g. `0.2`). |
| `title` | yes | Human display title (≤ 200 chars). |
| `description` | no | Short description (≤ 2000 chars). |
| `bundleRevision` | yes | The payload's OKF revision id (`rev-` + 12 hex). |
| `minimumOknollVersion` | no | Oldest runtime version expected to serve it. |
| `capabilities` | no | Unique strings; v1 defines `explore` and `ask`. |

**Strict producer, permissive consumer:** OkNoll-built images emit exactly the
fields above and must pass `validate_image_config`. Consumers preserve unknown
fields and unknown capabilities — they are never a rejection reason.

The machine-readable contract is `okf_core.image_spec.IMAGE_CONFIG_SCHEMA`
(JSON Schema draft 2020-12), shipped in the `okf-core` package.
