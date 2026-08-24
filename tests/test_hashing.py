from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from journal_ai.hashing import HashingError, hash_file, hash_text


def test_hash_file_matches_sha256_of_bytes(tmp_path: Path) -> None:
    file_path = tmp_path / "entry.md"
    file_path.write_text("Today was calm.", encoding="utf-8")

    expected = hashlib.sha256(b"Today was calm.").hexdigest()

    assert hash_file(file_path) == expected


def test_hash_file_reads_large_files_in_chunks(tmp_path: Path) -> None:
    file_path = tmp_path / "entry.md"
    content = b"line\n" * 5000
    file_path.write_bytes(content)

    assert (
        hash_file(file_path, chunk_size=8)
        == hashlib.sha256(content).hexdigest()
    )


def test_identical_content_hashes_identically(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("Same words.", encoding="utf-8")
    second.write_text("Same words.", encoding="utf-8")

    assert hash_file(first) == hash_file(second)


def test_different_content_hashes_differently(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("One thing.", encoding="utf-8")
    second.write_text("Another thing.", encoding="utf-8")

    assert hash_file(first) != hash_file(second)


def test_hash_text_matches_sha256_of_utf8() -> None:
    assert hash_text("Today was calm.") == hashlib.sha256(
        b"Today was calm."
    ).hexdigest()


def test_unreadable_file_raises_hashing_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(HashingError, match="Could not read file"):
        hash_file(missing)
