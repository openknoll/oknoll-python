"""The local catalog: aliases over immutable image identities (sqlite, WAL).

One flat alias namespace maps human names to pinned identities (OCI manifest
digest + OKF revision). Aliases are the only mutable pointers in the runtime;
everything they point at is content-addressed. WAL mode plus a busy timeout
carries concurrency between CLI writes and (later) daemon reads. Timestamps
are local operational metadata and never affect canonical identity.

Local image tags (``name:tag`` → manifest digest) live here too: they are the
same kind of mutable pointer, and the catalog is the runtime's one mutable
database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from oknoll_runtime.locator import ALIAS_RE, TAG_RE

CATALOG_FILENAME = "catalog.sqlite"
MODES = ("local", "remote", "checkout")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    alias TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('local', 'remote', 'checkout')),
    reference TEXT NOT NULL,
    oci_digest TEXT NOT NULL,
    okf_revision TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    signature_state TEXT NOT NULL DEFAULT 'unsigned',
    access TEXT NOT NULL DEFAULT 'local',
    lint_summary TEXT NOT NULL DEFAULT '',
    index_state TEXT NOT NULL DEFAULT 'none',
    installed_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS image_tags (
    name TEXT NOT NULL,
    tag TEXT NOT NULL,
    oci_digest TEXT NOT NULL,
    PRIMARY KEY (name, tag)
);
"""


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    alias: str
    mode: str
    reference: str
    oci_digest: str
    okf_revision: str
    title: str = ""
    description: str = ""
    publisher: str = ""
    signature_state: str = "unsigned"
    access: str = "local"
    lint_summary: str = ""
    index_state: str = "none"
    installed_at: str = field(default="", compare=False)
    last_used_at: str | None = field(default=None, compare=False)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Catalog:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / CATALOG_FILENAME
        data_dir.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        con.executescript(_SCHEMA)
        return con

    # -- aliases -----------------------------------------------------------

    def register(self, entry: CatalogEntry, *, update: bool = False) -> None:
        if not ALIAS_RE.match(entry.alias):
            raise CatalogError(
                f"invalid alias {entry.alias!r} (aliases match [a-z0-9][a-z0-9._-]*)"
            )
        if entry.mode not in MODES:
            raise CatalogError(f"invalid catalog mode {entry.mode!r}")
        with self._connect() as con:
            existing = con.execute(
                "SELECT alias FROM entries WHERE alias = ?", (entry.alias,)
            ).fetchone()
            if existing and not update:
                raise CatalogError(
                    f"alias {entry.alias!r} already exists — pass --update to repoint it"
                )
            con.execute(
                """
                INSERT INTO entries (
                    alias, mode, reference, oci_digest, okf_revision, title,
                    description, publisher, signature_state, access,
                    lint_summary, index_state, installed_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias) DO UPDATE SET
                    mode = excluded.mode,
                    reference = excluded.reference,
                    oci_digest = excluded.oci_digest,
                    okf_revision = excluded.okf_revision,
                    title = excluded.title,
                    description = excluded.description,
                    publisher = excluded.publisher,
                    signature_state = excluded.signature_state,
                    access = excluded.access,
                    lint_summary = excluded.lint_summary,
                    index_state = excluded.index_state,
                    installed_at = excluded.installed_at
                """,
                (
                    entry.alias,
                    entry.mode,
                    entry.reference,
                    entry.oci_digest,
                    entry.okf_revision,
                    entry.title,
                    entry.description,
                    entry.publisher,
                    entry.signature_state,
                    entry.access,
                    entry.lint_summary,
                    entry.index_state,
                    entry.installed_at or _now(),
                    entry.last_used_at,
                ),
            )

    def get(self, alias: str) -> CatalogEntry | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT alias, mode, reference, oci_digest, okf_revision, title, "
                "description, publisher, signature_state, access, lint_summary, "
                "index_state, installed_at, last_used_at FROM entries WHERE alias = ?",
                (alias,),
            ).fetchone()
        return CatalogEntry(*row) if row else None

    def list_entries(self) -> list[CatalogEntry]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT alias, mode, reference, oci_digest, okf_revision, title, "
                "description, publisher, signature_state, access, lint_summary, "
                "index_state, installed_at, last_used_at FROM entries ORDER BY alias"
            ).fetchall()
        return [CatalogEntry(*row) for row in rows]

    def remove(self, alias: str) -> None:
        with self._connect() as con:
            cursor = con.execute("DELETE FROM entries WHERE alias = ?", (alias,))
            if cursor.rowcount == 0:
                raise CatalogError(f"no installed bundle named {alias!r}")

    def touch_last_used(self, alias: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE entries SET last_used_at = ? WHERE alias = ?", (_now(), alias))

    def digests_in_use(self) -> set[str]:
        with self._connect() as con:
            rows = con.execute("SELECT oci_digest FROM entries").fetchall()
        return {row[0] for row in rows}

    # -- image tags --------------------------------------------------------

    def set_tag(self, name: str, tag: str, oci_digest: str) -> None:
        if not ALIAS_RE.match(name):
            raise CatalogError(f"invalid image name {name!r}")
        if not TAG_RE.match(tag):
            raise CatalogError(f"invalid image tag {tag!r}")
        with self._connect() as con:
            con.execute(
                "INSERT INTO image_tags (name, tag, oci_digest) VALUES (?, ?, ?) "
                "ON CONFLICT(name, tag) DO UPDATE SET oci_digest = excluded.oci_digest",
                (name, tag, oci_digest),
            )

    def resolve_tag(self, name: str, tag: str) -> str | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT oci_digest FROM image_tags WHERE name = ? AND tag = ?", (name, tag)
            ).fetchone()
        return row[0] if row else None

    def list_tags(self) -> list[tuple[str, str, str]]:
        """(name, tag, oci_digest) triples, sorted."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT name, tag, oci_digest FROM image_tags ORDER BY name, tag"
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def remove_tags_for(self, oci_digest: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM image_tags WHERE oci_digest = ?", (oci_digest,))
