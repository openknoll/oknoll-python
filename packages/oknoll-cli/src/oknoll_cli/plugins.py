"""`oknoll plugin` — list, inspect, validate installed connectors.

Only first-party connectors exist (the public connector marketplace is out of
scope), so "installed" means the connectors this build ships.
Validation is structural: does the class satisfy the frozen connector protocol?
The behavioral contract lives in the shared `ConnectorContractSuite`, which
every connector's own test suite must subclass.
"""

from __future__ import annotations

from typing import Any

from oknoll_connectors import FilesConnector, GitHubConnector, WebConnector

INSTALLED: dict[str, type[Any]] = {
    FilesConnector.id: FilesConnector,
    WebConnector.id: WebConnector,
    GitHubConnector.id: GitHubConnector,
}

_PROTOCOL_METHODS = ("probe", "acquire", "normalize", "checkpoint")


class PluginError(ValueError):
    """Unknown plugin name."""


def get(name: str) -> type[Any]:
    connector = INSTALLED.get(name)
    if connector is None:
        raise PluginError(f"no such connector: {name!r} (installed: {', '.join(INSTALLED)})")
    return connector


def describe(connector: type[Any]) -> dict[str, Any]:
    doc = (connector.__doc__ or "").strip().splitlines()
    return {
        "id": connector.id,
        "version": connector.version,
        "capabilities": sorted(connector.capabilities),
        "summary": doc[0] if doc else "",
        "module": connector.__module__,
    }


def validate(connector: type[Any]) -> list[str]:
    """Structural connector-protocol conformance problems (empty = conformant)."""
    problems: list[str] = []
    for attr in ("id", "version"):
        value = getattr(connector, attr, None)
        if not isinstance(value, str) or not value:
            problems.append(f"missing or empty class attribute {attr!r}")
    capabilities = getattr(connector, "capabilities", None)
    if not isinstance(capabilities, set | frozenset):
        problems.append("missing class attribute 'capabilities' (set of strings)")
    for method in _PROTOCOL_METHODS:
        if not callable(getattr(connector, method, None)):
            problems.append(f"missing protocol method {method!r}")
    return problems
