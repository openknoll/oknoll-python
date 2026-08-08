"""Shared connector contract suite (design §6.3).

Every connector — first-party or plugin — must pass this one suite. A
connector's test module subclasses ``ConnectorContractSuite`` and implements
``make_fixture``; pytest collects the ``test_*`` methods from the subclass.

Covered: probe support, provenance completeness, deterministic ordering,
normalization stability, checkpoint/resume, mid-stream cancellation, and
malformed input (must raise ConnectorError, never crash or emit garbage).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from okf_core import CanonicalDoc, SourceRef

from oknoll_connectors.protocol import (
    Connector,
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    RawItem,
)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class ConnectorFixture:
    """Everything the shared suite needs to exercise one connector."""

    make_connector: Callable[[ConnectorCheckpoint | None], Connector]
    source: SourceRef
    policy: FetchPolicy
    min_items: int  # the source must yield at least this many items (>= 2 for resume tests)
    malformed_source: SourceRef | None = None


class ConnectorContractSuite:
    """Subclass me per connector; implement ``make_fixture``."""

    def make_fixture(self, tmp_path: Path) -> ConnectorFixture:
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------

    def _acquire_all(self, fixture: ConnectorFixture) -> list[RawItem]:
        connector = fixture.make_connector(None)
        return list(connector.acquire(fixture.source, fixture.policy))

    # -- probe and provenance ---------------------------------------------

    def test_probe_reports_supported(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        connector = fixture.make_connector(None)
        result = connector.probe(fixture.source)
        assert result.supported, result.detail
        assert result.connector == connector.id

    def test_items_carry_full_provenance(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        connector = fixture.make_connector(None)
        items = list(connector.acquire(fixture.source, fixture.policy))
        assert len(items) >= fixture.min_items
        for item in items:
            assert item.uri
            assert item.media_type
            assert item.data
            assert _SHA256_RE.match(item.source_hash)
            assert _ISO_RE.match(item.retrieved_at)
            assert item.connector == connector.id
            assert item.connector_version == connector.version

    # -- determinism ------------------------------------------------------

    def test_acquisition_order_is_deterministic(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        first = self._acquire_all(fixture)
        second = self._acquire_all(fixture)
        signature = [(i.uri, i.media_type, i.source_hash) for i in first]
        assert signature == [(i.uri, i.media_type, i.source_hash) for i in second]
        assert signature == sorted(signature), "items must arrive in sorted uri order"

    def test_normalization_is_deterministic(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        connector = fixture.make_connector(None)
        items = list(connector.acquire(fixture.source, fixture.policy))
        first = [doc for item in items for doc in connector.normalize(item)]
        second = [doc for item in items for doc in connector.normalize(item)]
        assert [d.to_dict() for d in first] == [d.to_dict() for d in second]
        for doc in first:
            assert isinstance(doc, CanonicalDoc)
            assert doc.id
            assert doc.title
            assert doc.source_ref.connector == connector.id
            assert _SHA256_RE.match(doc.source_hash)
            assert doc.content_hash() == doc.content_hash()

    # -- checkpoint / resume / cancellation --------------------------------

    def test_checkpoint_resume_covers_everything_once(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        full = [i.uri for i in self._acquire_all(fixture)]
        assert len(full) >= 2, "resume test needs a source with at least two items"

        connector = fixture.make_connector(None)
        iterator = iter(connector.acquire(fixture.source, fixture.policy))
        consumed = [next(iterator).uri]
        checkpoint = connector.checkpoint()
        assert checkpoint is not None
        assert checkpoint.connector == connector.id

        resumed_connector = fixture.make_connector(checkpoint)
        resumed = [i.uri for i in resumed_connector.acquire(fixture.source, fixture.policy)]
        assert consumed + resumed == full, "resume must produce no duplicates and no gaps"

    def test_cancellation_midstream_leaves_valid_checkpoint(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        full = [i.uri for i in self._acquire_all(fixture)]

        connector = fixture.make_connector(None)
        iterator = connector.acquire(fixture.source, fixture.policy)
        consumed = [next(iter(iterator)).uri]
        # Cancellation is cooperative: the consumer stops iterating.
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
        checkpoint = connector.checkpoint()
        assert checkpoint is not None

        resumed = [
            i.uri
            for i in fixture.make_connector(checkpoint).acquire(fixture.source, fixture.policy)
        ]
        assert consumed + resumed == full

    def test_fresh_connector_has_no_checkpoint(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        assert fixture.make_connector(None).checkpoint() is None

    # -- malformed input ---------------------------------------------------

    def test_malformed_input_raises_connector_error(self, tmp_path: Path) -> None:
        fixture = self.make_fixture(tmp_path)
        if fixture.malformed_source is None:
            pytest.skip("connector fixture declares no malformed source")
        connector = fixture.make_connector(None)
        with pytest.raises(ConnectorError):
            for item in connector.acquire(fixture.malformed_source, fixture.policy):
                list(connector.normalize(item))
