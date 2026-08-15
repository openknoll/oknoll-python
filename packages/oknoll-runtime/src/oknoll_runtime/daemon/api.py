"""`/api/v1` — the daemon's versioned HTTP/JSON surface.

Thin request/response adapters over `DaemonService`. Bounds are the
Explorer's own `MAX_*` discipline (enforced inside okf-core); this layer only
parses parameters, maps `ServiceError` to the `{code, message, details}`
error schema, and keeps every service call in a worker thread so a slow model
ask never blocks the loop. No endpoint ever returns a host path.
"""

from __future__ import annotations

import json
from typing import Any

from okf_core import __version__ as core_version
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from oknoll_runtime.daemon.service import DaemonService, ServiceError

MAX_BODY_BYTES = 64 * 1024

_INT_PARAMS = ("limit", "lines", "max_chars")

# tool name -> (query parameter names, required parameter)
_TOOL_PARAMS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "overview": ((), None),
    "list": (("directory", "type", "tag", "status", "limit"), None),
    "search": (("query", "limit"), "query"),
    "peek": (("path", "lines"), "path"),
    "read": (("path", "max_chars"), "path"),
    "links": (("path", "direction", "limit"), "path"),
    "history": (("limit",), None),
}


def error_response(exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        {"code": exc.code, "message": str(exc), "details": {}}, status_code=exc.status
    )


def _bad_request(message: str) -> JSONResponse:
    return error_response(ServiceError("bad_request", message))


async def _json_body(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ServiceError("bad_request", "request body too large", status=413)
    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise ServiceError("bad_request", "request body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ServiceError("bad_request", "request body must be a JSON object")
    return parsed


def _str_or_none(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServiceError("bad_request", f"{key} must be a string")
    return value


def build_api_routes(service: DaemonService) -> list[Route]:
    async def health(request: Request) -> Response:
        entries = await run_in_threadpool(service.entries)
        return JSONResponse({"status": "ok", "version": core_version, "bundles": len(entries)})

    async def catalog(request: Request) -> Response:
        entries = await run_in_threadpool(service.entries)
        return JSONResponse({"bundles": [service.entry_dict(e) for e in entries]})

    async def bundle_tool(request: Request) -> Response:
        alias = request.path_params["alias"]
        tool = request.path_params["tool"]
        spec = _TOOL_PARAMS.get(tool)
        if spec is None:
            return error_response(
                ServiceError("unknown_tool", f"unknown tool {tool!r}", status=404)
            )
        param_names, required = spec
        kwargs: dict[str, Any] = {}
        for name in param_names:
            raw = request.query_params.get(name)
            if raw is None:
                continue
            if name in _INT_PARAMS:
                try:
                    kwargs[name] = int(raw)
                except ValueError:
                    return _bad_request(f"{name} must be an integer")
            else:
                kwargs[name] = raw
        if required is not None and required not in kwargs:
            return _bad_request(f"{tool} requires the {required!r} parameter")
        if tool == "search":
            kwargs["query"] = kwargs.pop("query", "")
        try:
            result = await run_in_threadpool(_call_tool, service, alias, tool, kwargs)
        except ServiceError as exc:
            return error_response(exc)
        return JSONResponse(result)

    async def create_session(request: Request) -> Response:
        try:
            payload = await _json_body(request)
            alias = _str_or_none(payload, "bundle")
            if alias is None:
                raise ServiceError("bad_request", "pass a bundle alias")
            mode = _str_or_none(payload, "mode") or "pd"
            session = await run_in_threadpool(lambda: service.create_session(alias, mode=mode))
        except ServiceError as exc:
            return error_response(exc)
        return JSONResponse(session.to_dict(), status_code=201)

    async def ask(request: Request) -> Response:
        try:
            payload = await _json_body(request)
            question = _str_or_none(payload, "question")
            if question is None:
                raise ServiceError("bad_request", "pass a question")
            alias = _str_or_none(payload, "bundle")
            session_id = _str_or_none(payload, "session")
            mode = _str_or_none(payload, "mode")
            model = _str_or_none(payload, "model")
            embedder = _str_or_none(payload, "embedder")
            result = await run_in_threadpool(
                lambda: service.ask(
                    question=question,
                    alias=alias,
                    session_id=session_id,
                    mode=mode,
                    model=model,
                    embedder=embedder,
                )
            )
        except ServiceError as exc:
            return error_response(exc)
        return JSONResponse(result)

    return [
        Route("/health", health, methods=["GET"]),
        Route("/catalog", catalog, methods=["GET"]),
        Route("/bundles/{alias}/{tool}", bundle_tool, methods=["GET"]),
        Route("/sessions", create_session, methods=["POST"]),
        Route("/ask", ask, methods=["POST"]),
    ]


def _call_tool(
    service: DaemonService, alias: str, tool: str, kwargs: dict[str, Any]
) -> dict[str, Any]:
    if tool == "search":
        query = str(kwargs.pop("query", ""))
        return service.call_tool(alias, "search", query, **kwargs)
    if tool in ("peek", "read", "links"):
        path = str(kwargs.pop("path"))
        return service.call_tool(alias, tool, path, **kwargs)
    if tool == "list":
        directory = str(kwargs.pop("directory", "concepts"))
        return service.call_tool(alias, "list", directory, **kwargs)
    return service.call_tool(alias, tool, **kwargs)
