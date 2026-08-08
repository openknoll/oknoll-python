"""`.env` loader semantics: parse the plain subset, never override the real environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from oknoll_providers import load_env


def _write_env(root: Path, text: str) -> None:
    (root / ".env").write_text(text, encoding="utf-8")


def test_basic_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OKNOLL_TEST_A", raising=False)
    monkeypatch.delenv("OKNOLL_TEST_B", raising=False)
    _write_env(tmp_path, "OKNOLL_TEST_A=hello\nOKNOLL_TEST_B = spaced \n")
    load_env(tmp_path)
    try:
        assert os.environ["OKNOLL_TEST_A"] == "hello"
        assert os.environ["OKNOLL_TEST_B"] == "spaced"
    finally:
        os.environ.pop("OKNOLL_TEST_A", None)
        os.environ.pop("OKNOLL_TEST_B", None)


def test_quotes_comments_and_export_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OKNOLL_TEST_Q1", "OKNOLL_TEST_Q2", "OKNOLL_TEST_E"):
        monkeypatch.delenv(key, raising=False)
    _write_env(
        tmp_path,
        "# a comment\n"
        "\n"
        'OKNOLL_TEST_Q1="double quoted"\n'
        "OKNOLL_TEST_Q2='single quoted'\n"
        "export OKNOLL_TEST_E=exported\n"
        "not a valid line\n"
        "=novalue\n",
    )
    load_env(tmp_path)
    try:
        assert os.environ["OKNOLL_TEST_Q1"] == "double quoted"
        assert os.environ["OKNOLL_TEST_Q2"] == "single quoted"
        assert os.environ["OKNOLL_TEST_E"] == "exported"
    finally:
        for key in ("OKNOLL_TEST_Q1", "OKNOLL_TEST_Q2", "OKNOLL_TEST_E"):
            os.environ.pop(key, None)


def test_existing_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKNOLL_TEST_WINS", "from-shell")
    _write_env(tmp_path, "OKNOLL_TEST_WINS=from-file\n")
    load_env(tmp_path)
    assert os.environ["OKNOLL_TEST_WINS"] == "from-shell"


def test_missing_file_is_a_no_op(tmp_path: Path) -> None:
    load_env(tmp_path)  # must not raise


def test_utf8_bom_does_not_hide_the_first_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OKNOLL_TEST_BOM", raising=False)
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfOKNOLL_TEST_BOM=first\n")
    load_env(tmp_path)
    try:
        assert os.environ["OKNOLL_TEST_BOM"] == "first"
    finally:
        os.environ.pop("OKNOLL_TEST_BOM", None)


def test_trailing_comments_are_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OKNOLL_TEST_C1", "OKNOLL_TEST_C2"):
        monkeypatch.delenv(key, raising=False)
    _write_env(
        tmp_path,
        'OKNOLL_TEST_C1="quoted value" # a comment\nOKNOLL_TEST_C2=unquoted value # a comment\n',
    )
    load_env(tmp_path)
    try:
        assert os.environ["OKNOLL_TEST_C1"] == "quoted value"
        assert os.environ["OKNOLL_TEST_C2"] == "unquoted value"
    finally:
        for key in ("OKNOLL_TEST_C1", "OKNOLL_TEST_C2"):
            os.environ.pop(key, None)


def test_invalid_utf8_raises_a_clean_provider_error(tmp_path: Path) -> None:
    from oknoll_providers import ProviderError

    (tmp_path / ".env").write_bytes(b"OKNOLL_BAD=caf\xe9\n")
    with pytest.raises(ProviderError, match="not valid UTF-8"):
        load_env(tmp_path)


def test_oversized_file_raises_a_clean_provider_error(tmp_path: Path) -> None:
    from oknoll_providers import ProviderError

    (tmp_path / ".env").write_bytes(b"#" * (300 * 1024))
    with pytest.raises(ProviderError, match="larger than"):
        load_env(tmp_path)


def test_null_bytes_and_unterminated_quotes_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("OKNOLL_TEST_NUL", "OKNOLL_TEST_UQ", "OKNOLL_TEST_OK"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_bytes(
        b'OKNOLL_TEST_NUL=has\x00null\nOKNOLL_TEST_UQ="unterminated\nOKNOLL_TEST_OK=fine\n'
    )
    load_env(tmp_path)
    try:
        assert "OKNOLL_TEST_NUL" not in os.environ
        assert "OKNOLL_TEST_UQ" not in os.environ
        assert os.environ["OKNOLL_TEST_OK"] == "fine"
    finally:
        for key in ("OKNOLL_TEST_NUL", "OKNOLL_TEST_UQ", "OKNOLL_TEST_OK"):
            os.environ.pop(key, None)
