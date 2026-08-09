# oknoll-providers

Real model and embedding providers (Anthropic API, local Ollama) behind
okf-core's provider seam, plus the `.env` loader and the provider spec grammar
(`stub | anthropic[:<model>] | ollama:<model>`). okf-core itself never touches
the network — everything that makes HTTP calls lives here.

Usually consumed through the [`oknoll`](https://pypi.org/project/oknoll/) CLI.
Part of [OpenKnoll](https://github.com/openknoll/oknoll-python).
