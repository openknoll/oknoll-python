"""SafeFetcher: FetchPolicy enforcement for network connectors (design §6.3, §17).

Every outbound request made by the web and GitHub connectors goes through
``SafeFetcher``, which enforces the whole network envelope of ``FetchPolicy``:

- scheme allowlist (HTTP/S only by default), no credentials in URLs;
- DNS resolution with an IP check on *every* resolved address — private,
  loopback, link-local (cloud metadata), multicast, reserved, unspecified and
  IPv4-mapped ranges are blocked unless ``allow_private_addresses`` is set;
- connections are pinned to the validated IP (the real transport connects to
  the checked address and sends SNI/Host for the original name), so a
  DNS-rebinding flip between check and connect cannot redirect the request;
- redirect cap with full re-validation of every hop;
- response size cap, bounded decompression, timeouts, per-host rate limits.

The resolver, transport, clock, and sleeper are injectable so the SSRF corpus
and connector tests run deterministically with no network.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from oknoll_connectors.protocol import ConnectorError, FetchPolicy

_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CHUNK = 65536


class FetchError(ConnectorError):
    """A fetch failed (network error, oversize response, bad redirect, ...)."""


class BlockedUrlError(FetchError):
    """The URL is refused by policy before any connection is made (SSRF guard)."""


Resolver = Callable[[str], tuple[str, ...]]


def default_resolver(host: str) -> tuple[str, ...]:
    """All addresses for `host`, deduplicated and sorted for determinism."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError(f"DNS resolution failed for {host!r}: {exc}") from exc
    return tuple(sorted({str(info[4][0]) for info in infos}))


def canonicalize_url(url: str, base: str | None = None) -> str:
    """Deterministic canonical form: resolve against `base`, lowercase scheme and
    host, drop the fragment and default ports, normalize the empty path to /."""
    if base is not None:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += f":{parts.password}"
        userinfo += "@"
    netloc = f"{userinfo}{host}"
    if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc += f":{parts.port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse `host` as an IP literal, including legacy IPv4 shorthand and
    decimal/octal/hex forms (2130706433, 0x7f000001, 017700000001, 127.1)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def address_block_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address must not be fetched, or None if it is globally routable."""
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return "loopback address"
    if addr.is_link_local:
        return "link-local address (cloud metadata range)"
    if addr.is_private:
        return "private address"
    if addr.is_multicast:
        return "multicast address"
    if addr.is_unspecified:
        return "unspecified address"
    if addr.is_reserved:
        return "reserved address"
    if not addr.is_global:
        return "non-global address"
    return None


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """One validated request: connect to `ip`, speak HTTP(S) for `host`."""

    method: str
    scheme: str
    host: str
    ip: str
    port: int
    path: str  # path + query, always starting with /
    headers: tuple[tuple[str, str], ...]
    timeout: float


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]  # lowercased header names
    body: bytes


class Transport(Protocol):
    """Sends one already-validated request; must return at most `max_bytes` bytes."""

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse: ...


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str  # canonical requested URL
    final_url: str  # canonical URL after redirects
    status: int
    media_type: str  # lowercased Content-Type without parameters
    charset: str | None
    headers: Mapping[str, str]
    data: bytes


class _RateLimiter:
    """Serializes requests per host at `requests_per_second`."""

    def __init__(
        self,
        requests_per_second: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if self._interval <= 0:
            return
        last = self._last.get(host)
        if last is not None:
            remaining = self._interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()


def _split_content_type(value: str) -> tuple[str, str | None]:
    media_type, _, rest = value.partition(";")
    charset = None
    for param in rest.split(";"):
        name, _, param_value = param.strip().partition("=")
        if name.strip().lower() == "charset":
            charset = param_value.strip().strip('"').lower() or None
    return media_type.strip().lower(), charset


def _bounded_decompress(data: bytes, wbits: int, limit: int, url: str) -> bytes:
    try:
        obj = zlib.decompressobj(wbits)
        out = obj.decompress(data, limit + 1)
    except zlib.error as exc:
        raise FetchError(f"invalid compressed response from {url}: {exc}") from exc
    if len(out) > limit or obj.unconsumed_tail:
        raise BlockedUrlError(
            f"decompressed response from {url} exceeds max_decompressed_bytes={limit}"
        )
    return out


@dataclass(slots=True)
class _Target:
    scheme: str
    host: str
    ip: str
    port: int
    path: str


class SafeFetcher:
    """Policy-enforcing HTTP client shared by the network connectors."""

    def __init__(
        self,
        policy: FetchPolicy,
        *,
        transport: Transport | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        user_agent: str = "oknoll (+https://github.com/tungamajunga/openknoll)",
    ) -> None:
        self.policy = policy
        self._transport = transport if transport is not None else HttpClientTransport()
        self._resolver = resolver if resolver is not None else default_resolver
        self._limiter = _RateLimiter(
            policy.per_host_requests_per_second,
            clock if clock is not None else time.monotonic,
            sleep if sleep is not None else time.sleep,
        )
        self._user_agent = user_agent

    # -- validation (the SSRF guard) --------------------------------------

    def validate(self, url: str) -> _Target:
        """Check `url` against policy and resolve it to a connectable target.

        Raises BlockedUrlError before any connection when the URL is refused.
        """
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        allowed = tuple(s.lower() for s in self.policy.allowed_schemes)
        if scheme not in allowed:
            raise BlockedUrlError(
                f"blocked {url!r}: scheme {scheme or '(none)'!r} not in allowed_schemes {allowed}"
            )
        if parts.username is not None or parts.password is not None:
            raise BlockedUrlError(f"blocked {url!r}: credentials in URLs are not allowed")
        host = parts.hostname
        if not host:
            raise BlockedUrlError(f"blocked {url!r}: no host")
        port = parts.port if parts.port is not None else _DEFAULT_PORTS[scheme]

        literal = _parse_ip_literal(host)
        if literal is not None:
            ips: tuple[str, ...] = (str(literal),)
        else:
            ips = self._resolver(host)
            if not ips:
                raise FetchError(f"DNS resolution failed for {host!r}: no addresses")

        if not self.policy.allow_private_addresses:
            for ip_text in ips:
                try:
                    addr = ipaddress.ip_address(ip_text)
                except ValueError as exc:
                    raise FetchError(f"resolver returned invalid address {ip_text!r}") from exc
                reason = address_block_reason(addr)
                if reason is not None:
                    raise BlockedUrlError(
                        f"blocked {url!r}: {host} resolves to {ip_text} ({reason})"
                    )

        path = parts.path or "/"
        if parts.query:
            path += f"?{parts.query}"
        return _Target(scheme=scheme, host=host, ip=ips[0], port=port, path=path)

    # -- fetching ----------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        method: str = "GET",
    ) -> FetchResponse:
        """Fetch `url`, re-validating every redirect hop. Returns the final
        response whatever its status; raises FetchError/BlockedUrlError on
        policy violations or transport failures."""
        requested = canonicalize_url(url)
        current = requested
        for _hop in range(self.policy.max_redirects + 1):
            target = self.validate(current)
            self._limiter.wait(target.host)
            request_headers: dict[str, str] = {
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip",
            }
            if headers:
                request_headers.update(headers)
            request = TransportRequest(
                method=method,
                scheme=target.scheme,
                host=target.host,
                ip=target.ip,
                port=target.port,
                path=target.path,
                headers=tuple(request_headers.items()),
                timeout=self.policy.timeout_seconds,
            )
            response = self._transport.send(request, self.policy.max_item_bytes + 1)

            if response.status in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(f"redirect from {current} carries no Location header")
                current = canonicalize_url(location, base=current)
                continue

            body = self._decode_body(response, current)
            if len(body) > self.policy.max_item_bytes:
                raise FetchError(
                    f"response from {current} exceeds max_item_bytes={self.policy.max_item_bytes}"
                )
            media_type, charset = _split_content_type(response.headers.get("content-type", ""))
            return FetchResponse(
                url=requested,
                final_url=current,
                status=response.status,
                media_type=media_type,
                charset=charset,
                headers=response.headers,
                data=body,
            )
        raise FetchError(f"{requested} exceeded max_redirects={self.policy.max_redirects}")

    def _decode_body(self, response: TransportResponse, url: str) -> bytes:
        encoding = response.headers.get("content-encoding", "").strip().lower()
        limit = self.policy.max_decompressed_bytes
        if encoding in ("", "identity"):
            return response.body
        if encoding in ("gzip", "x-gzip"):
            return _bounded_decompress(response.body, 16 + zlib.MAX_WBITS, limit, url)
        if encoding == "deflate":
            return _bounded_decompress(response.body, zlib.MAX_WBITS, limit, url)
        raise FetchError(f"unsupported Content-Encoding {encoding!r} from {url}")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pre-validated IP with SNI/verification for the
    original hostname — the DNS answer checked is the address connected to."""

    def __init__(
        self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip
        self._tls_context = context

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        self.sock = self._tls_context.wrap_socket(raw, server_hostname=self.host)


def _default_tls_context() -> ssl.SSLContext:
    """Verified TLS context using certifi's CA bundle — system Python installs
    (notably python.org macOS builds) often ship without usable root certs."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi is a declared dependency
        return ssl.create_default_context()


class HttpClientTransport:
    """Real transport: connects to the validated IP (never re-resolving the
    host) and presents the original hostname for TLS SNI and the Host header."""

    def __init__(self) -> None:
        self._tls_context = _default_tls_context()

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        conn: http.client.HTTPConnection
        if request.scheme == "https":
            conn = _PinnedHTTPSConnection(
                request.host, request.ip, request.port, request.timeout, self._tls_context
            )
        else:
            conn = http.client.HTTPConnection(request.ip, request.port, timeout=request.timeout)
        try:
            conn.putrequest(request.method, request.path, skip_host=True, skip_accept_encoding=True)
            host_header = request.host
            if request.port != _DEFAULT_PORTS.get(request.scheme):
                host_header = f"{request.host}:{request.port}"
            conn.putheader("Host", host_header)
            for name, value in request.headers:
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            chunks: list[bytes] = []
            remaining = max_bytes
            while remaining > 0:
                chunk = response.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            headers = {name.lower(): value for name, value in response.getheaders()}
            return TransportResponse(status=response.status, headers=headers, body=b"".join(chunks))
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError(
                f"fetch of {request.scheme}://{request.host}{request.path} failed: {exc}"
            ) from exc
        finally:
            conn.close()
