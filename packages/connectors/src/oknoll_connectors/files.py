"""Files connector: batch Markdown/text/PDF/Word acquisition.

Acquisition is deterministic (sorted paths, stable uris relative to the base
directory) and resumable via checkpoints. Normalization emits CanonicalDoc
records with heading/page/line anchors, embedded links, and source hashes —
never OKF.
"""

from __future__ import annotations

import fnmatch
import io
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from okf_core import Anchor, Block, CanonicalDoc, Link, SourceRef, sha256_hex
from okf_core import links as md_links

from oknoll_connectors.protocol import (
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    ProbeResult,
    RawItem,
)
from oknoll_connectors.textnorm import markdown_blocks as _markdown_blocks
from oknoll_connectors.textnorm import paragraphs_with_lines as _paragraphs_with_lines
from oknoll_connectors.textnorm import unique_slug as _unique_slug

MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FilesConnector:
    """Connector for local files and directories."""

    id = "files"
    version = "0.1.0"
    capabilities: ClassVar[set[str]] = {"markdown", "text", "pdf", "docx", "checkpoint"}

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        clock: Callable[[], str] | None = None,
        checkpoint: ConnectorCheckpoint | None = None,
        ignore_patterns: tuple[str, ...] = (),
    ) -> None:
        self.base_dir = (base_dir or Path.cwd()).resolve()
        self._clock = clock or _utc_now
        self._ignore_patterns = ignore_patterns
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
        path = self._resolve(source.uri)
        if path.is_file():
            if path.suffix.lower() in MEDIA_TYPES:
                return ProbeResult(True, self.id, "file", path.suffix.lower().lstrip("."))
            return ProbeResult(
                False, self.id, "file", f"unsupported extension {path.suffix or '(none)'}"
            )
        if path.is_dir():
            count = sum(1 for _ in self._walk(path, FetchPolicy()))
            if count:
                return ProbeResult(True, self.id, "directory", f"{count} supported file(s)")
            return ProbeResult(False, self.id, "directory", "no supported files found")
        return ProbeResult(False, self.id, "unknown", f"path does not exist: {source.uri}")

    def acquire(self, source: SourceRef, policy: FetchPolicy) -> Iterator[RawItem]:
        path = self._resolve(source.uri)
        if path.is_file():
            if path.suffix.lower() not in MEDIA_TYPES:
                raise ConnectorError(f"files: unsupported file type: {source.uri}")
            paths = [path]
        elif path.is_dir():
            paths = list(self._walk(path, policy))
        else:
            raise ConnectorError(f"files: source does not exist: {source.uri}")

        for file_path in paths:
            uri = self._uri_for(file_path)
            if self._resume_after is not None and uri <= self._resume_after:
                continue
            size = file_path.stat().st_size
            if size > policy.max_item_bytes:
                raise ConnectorError(
                    f"files: {uri} is {size} bytes (policy max_item_bytes={policy.max_item_bytes})"
                )
            data = file_path.read_bytes()
            item = RawItem(
                uri=uri,
                media_type=MEDIA_TYPES[file_path.suffix.lower()],
                data=data,
                source_hash=sha256_hex(data),
                retrieved_at=self._clock(),
                connector=self.id,
                connector_version=self.version,
                metadata={"filename": file_path.name, "bytes": size},
            )
            self._last_uri = uri  # set before yield: checkpoint() after next() must see it
            yield item

    def normalize(self, item: RawItem) -> Iterable[CanonicalDoc]:
        if item.media_type == "text/markdown":
            doc = self._normalize_markdown(item)
        elif item.media_type == "text/plain":
            doc = self._normalize_text(item)
        elif item.media_type == "application/pdf":
            doc = self._normalize_pdf(item)
        elif item.media_type == MEDIA_TYPES[".docx"]:
            doc = self._normalize_docx(item)
        else:
            raise ConnectorError(f"files: cannot normalize media type {item.media_type!r}")
        return [doc]

    def checkpoint(self) -> ConnectorCheckpoint | None:
        if self._last_uri is None:
            return None
        return ConnectorCheckpoint(
            connector=self.id, version=self.version, cursor={"last_uri": self._last_uri}
        )

    # -- acquisition helpers ----------------------------------------------

    def _resolve(self, uri: str) -> Path:
        path = Path(uri)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def _uri_for(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.base_dir).as_posix()
        except ValueError:
            return resolved.as_posix()

    def _ignored(self, rel_parts: tuple[str, ...]) -> bool:
        for pattern in self._ignore_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in rel_parts[:-1]:
                    return True
            elif any(fnmatch.fnmatch(part, pattern) for part in rel_parts):
                return True
        return False

    def _walk(self, root: Path, policy: FetchPolicy) -> Iterator[Path]:
        for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
            if not policy.follow_symlinks and path.is_symlink():
                continue
            if not path.is_file() or path.suffix.lower() not in MEDIA_TYPES:
                continue
            rel_parts = path.relative_to(root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            if self._ignored(rel_parts):
                continue
            yield path

    # -- normalization helpers --------------------------------------------

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
        try:
            return item.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorError(f"files: {item.uri} is not valid UTF-8: {exc}") from exc

    def _stem(self, item: RawItem) -> str:
        return Path(item.uri).stem.replace("-", " ").replace("_", " ").strip() or item.uri

    def _normalize_markdown(self, item: RawItem) -> CanonicalDoc:
        text = self._decode(item)
        blocks, anchors = _markdown_blocks(text)
        title = next(
            (b.text for b in blocks if b.kind == "heading" and b.level == 1),
            self._stem(item),
        )
        links = [Link(target=link.target, text=link.text) for link in md_links.extract_links(text)]
        return self._doc(item, title=title, blocks=blocks, anchors=anchors, links=links)

    def _normalize_text(self, item: RawItem) -> CanonicalDoc:
        text = self._decode(item)
        blocks: list[Block] = []
        anchors: list[Anchor] = []
        for start_line, paragraph in _paragraphs_with_lines(text):
            anchor_id = f"L{start_line}"
            anchors.append(Anchor(id=anchor_id, kind="line", value=str(start_line)))
            blocks.append(Block(kind="paragraph", text=paragraph, anchor_id=anchor_id))
        return self._doc(item, title=self._stem(item), blocks=blocks, anchors=anchors, links=[])

    def _normalize_pdf(self, item: RawItem) -> CanonicalDoc:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(io.BytesIO(item.data))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except (PyPdfError, ValueError, KeyError, OSError) as exc:
            raise ConnectorError(f"files: {item.uri} is not a readable PDF: {exc}") from exc

        blocks: list[Block] = []
        anchors: list[Anchor] = []
        for page_number, page_text in enumerate(pages, start=1):
            anchor_id = f"page-{page_number}"
            anchors.append(Anchor(id=anchor_id, kind="page", value=str(page_number)))
            first_on_page = True
            for _, paragraph in _paragraphs_with_lines(page_text):
                blocks.append(
                    Block(
                        kind="paragraph",
                        text=paragraph,
                        anchor_id=anchor_id if first_on_page else None,
                    )
                )
                first_on_page = False
        title = _pdf_title(item) or self._stem(item)
        return self._doc(item, title=title, blocks=blocks, anchors=anchors, links=[])

    def _normalize_docx(self, item: RawItem) -> CanonicalDoc:
        import zipfile

        import docx
        from docx.opc.exceptions import OpcError

        try:
            document = docx.Document(io.BytesIO(item.data))
        except (OpcError, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise ConnectorError(f"files: {item.uri} is not a readable .docx: {exc}") from exc

        blocks: list[Block] = []
        anchors: list[Anchor] = []
        used_slugs: set[str] = set()
        title: str | None = None
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or "") if paragraph.style is not None else ""
            level = _docx_heading_level(style)
            if level is not None:
                anchor_id = _unique_slug(text, used_slugs)
                anchors.append(Anchor(id=anchor_id, kind="heading", value=text))
                blocks.append(Block(kind="heading", text=text, level=level, anchor_id=anchor_id))
                if title is None:
                    title = text
            else:
                blocks.append(Block(kind="paragraph", text=text))
        return self._doc(
            item, title=title or self._stem(item), blocks=blocks, anchors=anchors, links=[]
        )


def _pdf_title(item: RawItem) -> str | None:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(item.data))
        meta = reader.metadata
    except Exception:  # metadata is best-effort; unreadable metadata is not an error
        return None
    if meta is not None and meta.title:
        title = str(meta.title).strip()
        return title or None
    return None


def _docx_heading_level(style_name: str) -> int | None:
    if style_name == "Title":
        return 1
    match = re.fullmatch(r"Heading (\d)", style_name)
    if match:
        return int(match.group(1))
    return None
