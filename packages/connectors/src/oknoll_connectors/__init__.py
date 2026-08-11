"""OpenKnoll connectors: acquire and normalize only — never write OKF.

Phase 2 shipped the connector protocol, the shared FetchPolicy and contract
suite, and the files connector (md/txt/PDF/docx). Phase 3 adds the web
connector (sitemap-first same-site crawl) and the GitHub connector
(commit-SHA provenance), both behind the SSRF-enforcing SafeFetcher.
"""

from oknoll_connectors.files import FilesConnector
from oknoll_connectors.github import GitHubConnector, parse_github_source
from oknoll_connectors.protocol import (
    Connector,
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    ProbeResult,
    RawItem,
    acquire_all,
)
from oknoll_connectors.web import WebConnector

__version__ = "0.3.1"

__all__ = [
    "Connector",
    "ConnectorCheckpoint",
    "ConnectorError",
    "FetchPolicy",
    "FilesConnector",
    "GitHubConnector",
    "ProbeResult",
    "RawItem",
    "WebConnector",
    "__version__",
    "acquire_all",
    "parse_github_source",
]
