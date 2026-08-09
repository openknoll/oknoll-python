# oknoll-eval

OpenKnoll evaluation harness: frozen benchmark specs (bundles + questions with
gold evidence) run against two retrieval conditions — progressive disclosure
via the deterministic explorer vs. a vector-RAG baseline — with grading,
metrics, and comparison reports. Drives `oknoll eval`.

Usually consumed through the [`oknoll`](https://pypi.org/project/oknoll/) CLI.
Part of [OpenKnoll](https://github.com/openknoll/oknoll-python).
