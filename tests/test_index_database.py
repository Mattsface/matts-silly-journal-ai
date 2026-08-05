from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from journal_ai.index_database import (
    LAST_INDEXED_AT_KEY,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    IndexDatabaseError,
    UnsupportedIndexSchemaError,
    delete_index_database,
    open_index_database,
)
from journal_ai.models import IndexedDocument

INDEXED_AT = datetime(2026, 7, 31, 13, 24, 18, tzinfo=UTC)


def make_document(
    relative_path: str,
    *,
    content_hash: str = "hash-1",
    file_size: int = 10,
) -> IndexedDocument:
    return IndexedDocument(
        relative_path=Path(relative_path),
        content_hash=content_hash,
        file_size=file_size,
        modified_at=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
        first_indexed_at=INDEXED_AT,
        last_indexed_at=INDEXED_AT,
    )


def table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()

    return {row[0] for row in rows}


def test_schema_is_created_automatically(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "index.sqlite"

    with open_index_database(database_path) as database:
        assert database.document_count() == 0
        assert database.read_documents() == {}
        assert database.read_last_indexed_at() is None

    assert database_path.is_file()
    assert {"documents", "index_metadata", "chunks"} <= table_names(database_path)


def test_schema_version_is_recorded(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path):
        pass

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT value FROM index_metadata WHERE key = ?",
            (SCHEMA_VERSION_KEY,),
        ).fetchone()
    finally:
        connection.close()

    assert row[0] == str(SCHEMA_VERSION)


def test_opening_an_existing_database_keeps_records(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        database.apply_changes(
            indexed_at=INDEXED_AT,
            inserted=[make_document("journals/entry.md")],
        )

    with open_index_database(database_path) as database:
        documents = database.read_documents()

    assert list(documents) == [Path("journals/entry.md")]
    assert documents[Path("journals/entry.md")].content_hash == "hash-1"


def test_insert_update_and_delete_are_persisted(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"
    later = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)

    with open_index_database(database_path) as database:
        database.apply_changes(
            indexed_at=INDEXED_AT,
            inserted=[
                make_document("journals/first.md"),
                make_document("journals/second.md"),
            ],
        )

        database.apply_changes(
            indexed_at=later,
            updated=[
                make_document(
                    "journals/first.md",
                    content_hash="hash-2",
                    file_size=42,
                )
            ],
            deleted=[Path("journals/second.md")],
        )

        documents = database.read_documents()
        assert list(documents) == [Path("journals/first.md")]

        first = documents[Path("journals/first.md")]
        assert first.content_hash == "hash-2"
        assert first.file_size == 42
        assert database.read_last_indexed_at() == later


def test_last_indexed_at_round_trips_as_utc(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        database.apply_changes(indexed_at=INDEXED_AT)
        stored = database.read_last_indexed_at()

    assert stored == INDEXED_AT
    assert stored is not None
    assert stored.tzinfo is not None


def test_failed_insert_rolls_the_transaction_back(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        database.apply_changes(
            indexed_at=INDEXED_AT,
            inserted=[make_document("journals/first.md")],
        )

        with pytest.raises(IndexDatabaseError, match="update failed"):
            database.apply_changes(
                indexed_at=datetime(2026, 8, 2, 8, 0, tzinfo=UTC),
                inserted=[
                    make_document("journals/second.md"),
                    make_document("journals/second.md"),
                ],
            )

        assert list(database.read_documents()) == [Path("journals/first.md")]
        assert database.read_last_indexed_at() == INDEXED_AT


def test_updating_a_missing_record_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        with pytest.raises(IndexDatabaseError, match="expected exactly one"):
            database.apply_changes(
                indexed_at=INDEXED_AT,
                inserted=[make_document("journals/first.md")],
                updated=[make_document("journals/missing.md")],
            )

        assert database.read_documents() == {}
        assert database.read_last_indexed_at() is None


def test_deleting_a_missing_record_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        with pytest.raises(IndexDatabaseError, match="expected exactly one"):
            database.apply_changes(
                indexed_at=INDEXED_AT,
                inserted=[make_document("journals/first.md")],
                deleted=[Path("journals/missing.md")],
            )

        assert database.read_documents() == {}


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path):
        pass

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE index_metadata SET value = ? WHERE key = ?",
            ("99", SCHEMA_VERSION_KEY),
        )
        connection.commit()
    finally:
        connection.close()

    with (
        pytest.raises(UnsupportedIndexSchemaError, match="schema version 99"),
        open_index_database(database_path),
    ):
        pass


def test_corrupt_database_file_is_reported(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"
    database_path.write_bytes(b"this is not a database")

    with (
        pytest.raises(IndexDatabaseError),
        open_index_database(database_path),
    ):
        pass


def test_invalid_stored_timestamp_is_reported(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        database.apply_changes(indexed_at=INDEXED_AT)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE index_metadata SET value = ? WHERE key = ?",
            ("not-a-timestamp", LAST_INDEXED_AT_KEY),
        )
        connection.commit()
    finally:
        connection.close()

    with (
        open_index_database(database_path) as database,
        pytest.raises(IndexDatabaseError, match="invalid timestamp"),
    ):
        database.read_last_indexed_at()


def test_delete_index_database_removes_file_and_sidecars(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "index.sqlite"
    write_ahead_log = tmp_path / "index.sqlite-wal"
    shared_memory = tmp_path / "index.sqlite-shm"

    with open_index_database(database_path):
        pass

    write_ahead_log.write_bytes(b"")
    shared_memory.write_bytes(b"")

    delete_index_database(database_path)

    assert not database_path.exists()
    assert not write_ahead_log.exists()
    assert not shared_memory.exists()


def test_delete_index_database_ignores_missing_file(tmp_path: Path) -> None:
    delete_index_database(tmp_path / "index.sqlite")


def test_delete_index_database_refuses_a_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"
    database_path.mkdir()

    with pytest.raises(IndexDatabaseError, match="Refusing to delete"):
        delete_index_database(database_path)

    assert database_path.is_dir()
