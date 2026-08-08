# Zephyr Wire Protocol

## Reference Sheet, Scope, and Conventions

**Version:** 0.3-draft
**Contract string:** `zephyr/v0.3`
**Status:** Working draft
**Audience:** Platform teams, integration engineers, observability owners.

Normative keywords follow RFC 2119 when used in all capitals.

---

## 1. Scope

This sheet fixes the wire-level contract for Zephyr endpoints. It does not
cover deployment topology or credential rotation.

## 2. Conventions

Field names are lower-camel-case. Timestamps are RFC 3339 UTC.

## 3. What is Zephyr?

Zephyr is a governed telemetry mesh: agents publish signed measurement frames,
and Zephyr brokers replicate them to every subscribed region with at-least-once
delivery. A Zephyr frame carries its own provenance envelope, so consumers can
audit who measured what, and when.

Frames older than the retention window are compacted into daily rollups.

## 4. Frame layout

| Field | Meaning |
|---|---|
| `seq` | Monotonic per-publisher sequence |
| `sig` | Publisher signature over the payload |

## 5. Error codes

- `Z-401` — unsigned frame
- `Z-409` — sequence conflict
