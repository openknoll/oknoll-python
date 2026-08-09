# oknoll-connectors

OpenKnoll first-party source connectors — files (md, txt, PDF, docx), web
(sitemap-first, same-site), and GitHub — plus the plugin SDK and the shared
`ConnectorContractSuite` every connector must pass. All network I/O goes
through `SafeFetcher` (SSRF enforcement); connectors acquire and normalize
only, they never write OKF.

Usually consumed through the [`oknoll`](https://pypi.org/project/oknoll/) CLI.
Part of [OpenKnoll](https://github.com/openknoll/oknoll-python).
