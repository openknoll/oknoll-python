# okf-core

OpenKnoll core library: the `CanonicalDoc` model, OKF v0.2 parser/writer,
5-level lint, build pipeline (acquire → normalize → plan → generate → link →
lint → index → publish), deterministic explorer, ask policy, RAG seam, and
packer. Structurally network-free — real providers live in `oknoll-providers`.

Usually consumed through the [`oknoll`](https://pypi.org/project/oknoll/) CLI.
Part of [OpenKnoll](https://github.com/openknoll/oknoll-python).
