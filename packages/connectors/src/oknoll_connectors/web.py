"""Web connector: bounded seeded same-site crawl (design §6.1 priority 2).

MVP behavior per the design table: sitemap first, canonical URLs, robots and
per-host rate limits respected, 100-page default cap. Every request goes
through ``SafeFetcher`` so the whole ``FetchPolicy`` network envelope (SSRF
guard included) applies. Authenticated and JS-heavy sites are deferred.

Determinism: the crawl frontier is processed in a stable order (seed, then
sorted sitemap URLs, then per-page discovered links in sorted order), and
acquired pages are yielded in sorted canonical-URL order — which is also what
makes checkpoint/resume a simple "skip up to the last yielded URL" rule.
"""

from __future__ import annotations

import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import ClassVar
from urllib.parse import urlsplit

from okf_core import Anchor, Block, CanonicalDoc, Link, SourceRef, sha256_hex
from okf_core import links as md_links

from oknoll_connectors.fetch import FetchError, FetchResponse, SafeFetcher, canonicalize_url
from oknoll_connectors.protocol import (
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    ProbeResult,
    RawItem,
)
from oknoll_connectors.textnorm import (
    markdown_blocks,
    paragraphs_with_lines,
    unique_slug,
)

DEFAULT_MAX_PAGES = 100
_MAX_SITEMAPS = 10
_TEXT_MEDIA_TYPES = {"text/html", "text/plain", "text/markdown"}
_ROBOTS_AGENT = "oknoll"


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_fetcher_factory(policy: FetchPolicy) -> SafeFetcher:
    return SafeFetcher(policy)


class WebConnector:
    """Connector for public websites."""

    id = "web"
    version = "0.1.0"
    capabilities: ClassVar[set[str]] = {"html", "sitemap", "robots", "checkpoint"}

    def __init__(
        self,
        *,
        fetcher_factory: Callable[[FetchPolicy], SafeFetcher] | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        clock: Callable[[], str] | None = None,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> None:
        self._fetcher_factory = fetcher_factory or default_fetcher_factory
        self._max_pages = max_pages
        self._clock = clock or _utc_now
        self._resume_after: str | None = None
        self._last_uri: str | None = None
        if checkpoint is not None:
            if checkpoint.connector != self.id:
                raise ConnectorError(
                    f"checkpoint belongs to connector {checkpoint.connector!r}, not {self.id!r}"
                )
            cursor_uri = checkpoint.cursor.get("last_uri")
            if isinstance(cursor_uri, str):
                self._resume_after = cursor_uri
                self._last_uri = cursor_uri

    # -- protocol ---------------------------------------------------------

    def probe(self, source: SourceRef) -> ProbeResult:
        """Offline probe: URL shape only — acquisition applies the full policy."""
        parts = urlsplit(source.uri.strip())
        if parts.scheme.lower() not in ("http", "https"):
            return ProbeResult(False, self.id, "unknown", f"not an http(s) URL: {source.uri}")
        if not parts.hostname:
            return ProbeResult(False, self.id, "url", "URL has no host")
        return ProbeResult(True, self.id, "url", f"same-site crawl of {parts.hostname}")

    def acquire(self, source: SourceRef, policy: FetchPolicy) -> Iterator[RawItem]:
        fetcher = self._fetcher_factory(policy)
        seed = canonicalize_url(source.uri)
        host = urlsplit(seed).hostname
        if not host:
            raise ConnectorError(f"web: not a crawlable URL: {source.uri}")

        pages = self._crawl(fetcher, seed, host)
        for uri in sorted(pages):
            if self._resume_after is not None and uri <= self._resume_after:
                continue
            response = pages[uri]
            item = RawItem(
                uri=uri,
                media_type=response.media_type,
                data=response.data,
                source_hash=sha256_hex(response.data),
                retrieved_at=self._clock(),
                connector=self.id,
                connector_version=self.version,
                metadata={
                    "requested_url": response.url,
                    "charset": response.charset or "utf-8",
                    "bytes": len(response.data),
                },
            )
            self._last_uri = uri  # set before yield: checkpoint() after next() must see it
            yield item

    def normalize(self, item: RawItem) -> Iterable[CanonicalDoc]:
        if item.media_type == "text/html":
            doc = self._normalize_html(item)
        elif item.media_type == "text/markdown":
            doc = self._normalize_markdown(item)
        elif item.media_type == "text/plain":
            doc = self._normalize_text(item)
        else:
            raise ConnectorError(f"web: cannot normalize media type {item.media_type!r}")
        return [doc]

    def checkpoint(self) -> ConnectorCheckpoint | None:
        if self._last_uri is None:
            return None
        return ConnectorCheckpoint(
            connector=self.id, version=self.version, cursor={"last_uri": self._last_uri}
        )

    # -- crawling ---------------------------------------------------------

    def _crawl(self, fetcher: SafeFetcher, seed: str, host: str) -> dict[str, FetchResponse]:
        root = canonicalize_url(f"{urlsplit(seed).scheme}://{host}/")
        robots, sitemap_urls = self._read_robots(fetcher, root)
        frontier_seeds = [
            seed,
            *sorted(
                url
                for url in self._read_sitemaps(fetcher, robots, root, sitemap_urls)
                if self._in_scope(url, host) and url != seed
            ),
        ]

        queue: deque[str] = deque(frontier_seeds)
        seen: set[str] = set(frontier_seeds)
        pages: dict[str, FetchResponse] = {}

        while queue and len(pages) < self._max_pages:
            url = queue.popleft()
            if robots is not None and not robots.can_fetch(_ROBOTS_AGENT, url):
                if url == seed:
                    raise ConnectorError(f"web: robots.txt disallows the seed URL {seed}")
                continue
            try:
                response = fetcher.fetch(url)
            except FetchError as exc:
                if url == seed:
                    raise ConnectorError(f"web: seed fetch failed: {exc}") from exc
                continue  # a hostile or broken link never kills the crawl
            final = canonicalize_url(response.final_url)
            if response.status != 200:
                if url == seed:
                    raise ConnectorError(f"web: seed URL {seed} returned HTTP {response.status}")
                continue
            if not self._in_scope(final, host):
                continue  # redirected off-site
            if final in pages:
                continue
            if response.media_type not in _TEXT_MEDIA_TYPES:
                continue
            pages[final] = response
            if response.media_type == "text/html":
                discovered = sorted(
                    {
                        target
                        for href, _text in _extract_html(response).links
                        if (target := _crawlable_target(href, final)) is not None
                        and self._in_scope(target, host)
                    }
                )
                for target in discovered:
                    if target not in seen:
                        seen.add(target)
                        queue.append(target)

        if not pages:
            raise ConnectorError(f"web: crawl of {seed} produced no usable pages")
        return pages

    @staticmethod
    def _in_scope(url: str, host: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and parts.hostname == host

    def _read_robots(
        self, fetcher: SafeFetcher, root: str
    ) -> tuple[urllib.robotparser.RobotFileParser | None, list[str]]:
        try:
            response = fetcher.fetch(canonicalize_url("/robots.txt", base=root))
        except FetchError:
            return None, []
        if response.status != 200:
            return None, []
        text = response.data.decode("utf-8", errors="replace")
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        sitemaps = parser.site_maps() or []
        return parser, list(sitemaps)

    def _read_sitemaps(
        self,
        fetcher: SafeFetcher,
        robots: urllib.robotparser.RobotFileParser | None,
        root: str,
        declared: list[str],
    ) -> list[str]:
        """Page URLs from declared sitemaps (or the /sitemap.xml convention),
        following one level of sitemap-index nesting, bounded and deterministic."""
        candidates = sorted(canonicalize_url(url, base=root) for url in declared) or [
            canonicalize_url("/sitemap.xml", base=root)
        ]
        page_urls: list[str] = []
        fetched = 0
        queue = deque(candidates)
        while queue and fetched < _MAX_SITEMAPS:
            sitemap_url = queue.popleft()
            fetched += 1
            try:
                response = fetcher.fetch(sitemap_url)
            except FetchError:
                continue
            if response.status != 200:
                continue
            nested, urls = _parse_sitemap(response.data)
            page_urls.extend(canonicalize_url(url, base=sitemap_url) for url in urls)
            for child in sorted(nested):
                queue.append(canonicalize_url(child, base=sitemap_url))
        return page_urls

    # -- normalization ----------------------------------------------------

    def _doc(
        self,
        item: RawItem,
        *,
        title: str,
        blocks: list[Block],
        anchors: list[Anchor],
        links: list[Link],
    ) -> CanonicalDoc:
        return CanonicalDoc(
            id=f"doc-{sha256_hex(item.uri)[:12]}",
            source_ref=SourceRef(connector=self.id, uri=item.uri),
            source_hash=item.source_hash,
            retrieved_at=item.retrieved_at,
            title=title,
            blocks=tuple(blocks),
            anchors=tuple(anchors),
            links=tuple(links),
            metadata={"media_type": item.media_type, **item.metadata},
        )

    def _decode(self, item: RawItem) -> str:
        charset = str(item.metadata.get("charset", "utf-8"))
        try:
            return item.data.decode(charset, errors="replace")
        except LookupError:
            return item.data.decode("utf-8", errors="replace")

    def _title_fallback(self, uri: str) -> str:
        path = urlsplit(uri).path
        stem = PurePosixPath(path).stem if path not in ("", "/") else ""
        return stem.replace("-", " ").replace("_", " ").strip() or urlsplit(uri).hostname or uri

    def _normalize_html(self, item: RawItem) -> CanonicalDoc:
        extraction = _HtmlExtraction()
        parser = _HtmlBlockParser(extraction)
        parser.feed(self._decode(item))
        parser.close()
        title = extraction.title or next(
            (b.text for b in extraction.blocks if b.kind == "heading" and b.level == 1),
            self._title_fallback(item.uri),
        )
        links = [
            Link(target=target, text=text.strip())
            for href, text in extraction.links
            if (target := _crawlable_target(href, item.uri)) is not None
        ]
        return self._doc(
            item, title=title, blocks=extraction.blocks, anchors=extraction.anchors, links=links
        )

    def _normalize_markdown(self, item: RawItem) -> CanonicalDoc:
        text = self._decode(item)
        blocks, anchors = markdown_blocks(text)
        title = next(
            (b.text for b in blocks if b.kind == "heading" and b.level == 1),
            self._title_fallback(item.uri),
        )
        links = [Link(target=link.target, text=link.text) for link in md_links.extract_links(text)]
        return self._doc(item, title=title, blocks=blocks, anchors=anchors, links=links)

    def _normalize_text(self, item: RawItem) -> CanonicalDoc:
        text = self._decode(item)
        blocks: list[Block] = []
        anchors: list[Anchor] = []
        for start_line, paragraph in paragraphs_with_lines(text):
            anchor_id = f"L{start_line}"
            anchors.append(Anchor(id=anchor_id, kind="line", value=str(start_line)))
            blocks.append(Block(kind="paragraph", text=paragraph, anchor_id=anchor_id))
        return self._doc(
            item, title=self._title_fallback(item.uri), blocks=blocks, anchors=anchors, links=[]
        )


def _crawlable_target(href: str, base: str) -> str | None:
    """Resolve `href` against `base`; None for non-http(s) targets (mailto,
    javascript, data, ...)."""
    stripped = href.strip()
    if not stripped:
        return None
    resolved = canonicalize_url(stripped, base=base)
    if urlsplit(resolved).scheme not in ("http", "https"):
        return None
    return resolved


def _parse_sitemap(data: bytes) -> tuple[list[str], list[str]]:
    """(nested sitemap URLs, page URLs) from sitemap XML. Bodies carrying DTDs
    or entity declarations are rejected outright (entity-expansion attacks)."""
    head = data[:4096].lstrip().lower()
    if b"<!doctype" in head or b"<!entity" in head:
        return [], []
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return [], []
    tag = root.tag.rsplit("}", 1)[-1].lower()
    locs = [
        text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1].lower() == "loc" and (text := element.text)
    ]
    if tag == "sitemapindex":
        return locs, []
    return [], locs


def _extract_html(response: FetchResponse) -> _HtmlExtraction:
    extraction = _HtmlExtraction()
    parser = _HtmlBlockParser(extraction)
    charset = response.charset or "utf-8"
    try:
        text = response.data.decode(charset, errors="replace")
    except LookupError:
        text = response.data.decode("utf-8", errors="replace")
    parser.feed(text)
    parser.close()
    return extraction


class _HtmlExtraction:
    """Blocks, anchors, title, and raw (href, text) pairs pulled from one page."""

    def __init__(self) -> None:
        self.title: str | None = None
        self.blocks: list[Block] = []
        self.anchors: list[Anchor] = []
        self.links: list[tuple[str, str]] = []


_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _HtmlBlockParser(HTMLParser):
    """Deterministic block-level HTML extraction. Text is captured inside
    semantic containers (headings, p, li, pre, blockquote, td/th, title); layout
    and script content is ignored. Not a rendering engine and does not need to
    be — anchors, ordering, and link capture are what downstream stages use."""

    def __init__(self, extraction: _HtmlExtraction) -> None:
        super().__init__(convert_charrefs=True)
        self._out = extraction
        self._used_slugs: set[str] = set()
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._capture: list[str] | None = None
        self._heading_level = 0
        self._list_items: list[str] | None = None
        self._link_href: str | None = None
        self._link_text: list[str] = []

    # -- tag handling ------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "a":
            href = next((value for name, value in attrs if name == "href"), None)
            if href:
                self._flush_link()
                self._link_href = href
            return
        if tag == "br":
            if self._capture is not None:
                self._capture.append("\n")
            return
        if tag in _HEADING_TAGS:
            self._begin_capture()
            self._heading_level = int(tag[1])
            return
        if tag in ("ul", "ol"):
            self._end_capture()
            self._list_items = []
            return
        if tag == "li":
            if self._list_items is not None:
                self._begin_capture()
            return
        if tag in ("p", "pre", "blockquote", "td", "th"):
            self._begin_capture()
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            if self._out.title is None:
                title = " ".join("".join(self._title_parts).split())
                self._out.title = title or None
            return
        if tag == "a":
            self._flush_link()
            return
        if tag in _HEADING_TAGS:
            text = self._end_capture()
            if text:
                anchor_id = unique_slug(text, self._used_slugs)
                self._out.anchors.append(Anchor(id=anchor_id, kind="heading", value=text))
                self._out.blocks.append(
                    Block(kind="heading", text=text, level=self._heading_level, anchor_id=anchor_id)
                )
            self._heading_level = 0
            return
        if tag == "li":
            text = self._end_capture()
            if text and self._list_items is not None:
                self._list_items.append(f"- {text}")
            return
        if tag in ("ul", "ol"):
            if self._list_items:
                self._out.blocks.append(Block(kind="list", text="\n".join(self._list_items)))
            self._list_items = None
            return
        if tag == "pre":
            text = self._end_capture(collapse=False)
            if text:
                self._out.blocks.append(Block(kind="code", text=text))
            return
        if tag == "blockquote":
            text = self._end_capture()
            if text:
                self._out.blocks.append(Block(kind="quote", text=text))
            return
        if tag in ("p", "td", "th"):
            text = self._end_capture()
            if text:
                self._out.blocks.append(Block(kind="paragraph", text=text))
            return

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._link_href is not None:
            self._link_text.append(data)
        if self._capture is not None:
            self._capture.append(data)

    def close(self) -> None:
        super().close()
        self._flush_link()
        self._end_capture()

    # -- capture helpers ---------------------------------------------------

    def _begin_capture(self) -> None:
        self._end_capture()
        self._capture = []

    def _end_capture(self, *, collapse: bool = True) -> str:
        if self._capture is None:
            return ""
        raw = "".join(self._capture)
        self._capture = None
        if collapse:
            return " ".join(raw.split())
        return raw.strip("\n")

    def _flush_link(self) -> None:
        if self._link_href is not None:
            self._out.links.append((self._link_href, " ".join("".join(self._link_text).split())))
        self._link_href = None
        self._link_text = []
