# Fixtures

Golden fixtures are the backbone of the test suite: frozen inputs → expected trees, reports,
and checksums. Never edit a golden bundle casually — regenerating checksums is part of the
change, and CI diffs will catch drift.

- `bundles/golden/minimal/` — a valid OKF v0.2 bundle with provenance, footnote claims, a
  correct manifest, and a complete link graph. Lints with **zero findings**.
- `bundles/golden/unknown-fields/` — a "foreign" bundle with an unknown concept type and
  unknown frontmatter keys. Exercises the permissive-consumer posture (ADR-0003): tolerated
  by lint, and its unknown fields must survive parse → write round trips byte-stably.
- `bundles/golden/multihop/` — three linked concepts where the answer to "Who must sign
  off a production release?" lives one hop *past* the concept lexical search finds. The
  Phase 4 exit-gate test asserts the second hop is reached through `links`, cited by path,
  and warned about (it is draft + unverified). Lints with **zero findings**.
- `bundles/malformed/<case>/` — one bundle per failure class. Each contains
  `expected-findings.json` with the finding codes that must appear plus the expected
  default/strict pass verdicts. The test suite discovers these automatically — add a new
  case by adding a directory, no test change needed.

- `bundles/upstream/` — the upstream Open Knowledge Format v0.2 reference bundles, vendored
  verbatim (see its README for the pinned commit). Consumed as foreign bundles: lint must
  never crash on them, and round trips must preserve their fields.

- `sources/handbook/` — frozen connector/pipeline inputs covering every files-connector
  format (md, txt, PDF, docx). The binaries are generated deterministically (zeroed zip
  timestamps, hand-built PDF); regenerating them is a fixture change and shows up in test
  diffs. The pipeline exit-gate tests build this directory into a bundle that must lint
  with zero findings and rebuild byte-identically.
- `sources/malformed/` — corrupt PDF/docx inputs; connectors must raise ConnectorError,
  never crash or emit garbage.
- `sources/website/` — the fixture site for the web connector, served offline by a fake
  transport as `https://example.test`. Covers robots.txt (with a disallowed `/private/`
  area), a sitemap, HTML/Markdown/plain-text pages, a redirect, and offsite/mailto links
  that must never be crawled. The remote pipeline exit-gate test builds it into a bundle
  that lints with zero findings and rebuilds byte-identically.
- `security/ssrf/cases.json` — the SSRF corpus (release gate): URLs SafeFetcher must
  refuse before any connection (schemes, loopback/private/link-local/metadata addresses,
  encoded IP forms, DNS rebinding, credentials-in-URL) plus positive controls that must
  stay fetchable. Add a case by editing the JSON — the suite parametrizes over it.
- `security/prompt-injection/` — the prompt-injection corpus (release gate): a hostile
  `bundle/` whose text tries to override instructions, coerce tool and shell calls, escape
  the bundle root through links, exfiltrate to the network, and spoof verification;
  `cases.json` pairs each hostile string or link with the invariant it must not break.
  Bundle text is data — it may be quoted back as evidence, never obeyed. Add a case by
  editing the JSON (each `expect` value maps to one assertion branch in the suite).
