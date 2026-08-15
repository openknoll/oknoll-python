# oknoll-runtime

The OpenKnoll local runtime: a machine-level content-addressed store for
OkNoll images (OCI artifacts whose payload is one OKF bundle), deterministic
image build/save/load, the sqlite catalog of installed bundles, the unified
bundle locator parser, and platform directory resolution.

Everything here is local and offline; registry and cloud clients build on top
of it. Usually consumed through the
[`oknoll`](https://pypi.org/project/oknoll/) CLI.
Part of [OpenKnoll](https://github.com/openknoll/oknoll-python).
