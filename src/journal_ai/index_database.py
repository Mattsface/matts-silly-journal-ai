from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from journal_ai.models import IndexedDocument

SCHEMA_VERSION = 1

SCHEMA_VERSION_KEY = "schema_version"
LAST_INDEXED_AT_KEY = "last_indexed_at"

# SQLite may keep a write-ahead log and a shared-memory file beside the
# database. They must be removed together with it during a rebuild.
DATABASE_SIDECAR_SUFFIXES = ("-wal", "-shm")

_CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS index_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_CREATE_DOCUMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    first_indexed_at TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL
)
"""

_SELECT_DOCUMENTS = """
SELECT
    relative_path,
    content_hash,
    file_size,
    modified_at,
    first_indexed_at,
    last_indexed_at
FROM documents
ORDER BY relative_path
"""

_INSERT_DOCUMENT = """
INSERT INTO documents (
    relative_path,
    content_hash,
    file_size,
    modified_at,
    first_indexed_at,
    last_indexed_at
) VALUES (?, ?, ?, ?, ?, ?)
"""

_UPDATE_DOCUMENT = """
UPDATE documents
SET
    content_hash = ?,
    file_size = ?,
    modified_at = ?,
    last_indexed_at = ?
WHERE relative_path = ?
"""

_DELETE_DOCUMENT = "DELETE FROM documents WHERE relative_path = ?"

_UPSERT_METADATA = """
INSERT INTO index_metadata (key, value) VALUES (?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""

_SELECT_METADATA = "SELECT value FROM index_metadata WHERE key = ?"


class IndexDatabaseError(Exception):
    """Raised when the SQLite index cannot be opened, read, or updated."""


class UnsupportedIndexSchemaError(IndexDatabaseError):
    """Raised when the database uses an incompatible schema version."""


class IndexDatabase:
    """Explicit SQLite access to the journal file inventory.

    Instances are created by open_index_database so every connection is
    closed again. No connection is shared at module level.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
    ) -> None:
        self._connection = connection
        self.database_path = database_path

    def initialize(self) -> None:
        """Create the schema when missing and reject foreign schemas."""
        with self._transaction() as cursor:
            cursor.execute(_CREATE_METADATA_TABLE)

            stored_version = _fetch_optional_text(
                cursor,
                _SELECT_METADATA,
                (SCHEMA_VERSION_KEY,),
            )

            if stored_version is None:
                cursor.execute(
                    _UPSERT_METADATA,
                    (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
                )
            elif stored_version != str(SCHEMA_VERSION):
                raise UnsupportedIndexSchemaError(
                    f"Index database {self.database_path} uses schema version "
                    f"{stored_version}, but version {SCHEMA_VERSION} is "
                    "required. Rebuild it with: journal-ai index --rebuild"
                )

            cursor.execute(_CREATE_DOCUMENTS_TABLE)

    def read_documents(self) -> dict[Path, IndexedDocument]:
        """Return every indexed document keyed by its relative path."""
        with self._read_cursor() as cursor:
            rows = cursor.execute(_SELECT_DOCUMENTS).fetchall()

        documents = (self._row_to_document(row) for row in rows)
        return {document.relative_path: document for document in documents}

    def document_count(self) -> int:
        """Return how many documents the index currently tracks."""
        with self._read_cursor() as cursor:
            row = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()

        return int(row[0])

    def read_last_indexed_at(self) -> datetime | None:
        """Return when the last successful index run finished."""
        with self._read_cursor() as cursor:
            value = _fetch_optional_text(
                cursor,
                _SELECT_METADATA,
                (LAST_INDEXED_AT_KEY,),
            )

        if value is None:
            return None

        return self._parse_timestamp(value)

    def apply_changes(
        self,
        *,
        indexed_at: datetime,
        inserted: Sequence[IndexedDocument] = (),
        updated: Sequence[IndexedDocument] = (),
        deleted: Sequence[Path] = (),
    ) -> None:
        """Apply one prepared set of index changes in a single transaction."""
        with self._transaction() as cursor:
            for document in inserted:
                cursor.execute(
                    _INSERT_DOCUMENT,
                    (
                        document.relative_path.as_posix(),
                        document.content_hash,
                        document.file_size,
                        _format_timestamp(document.modified_at),
                        _format_timestamp(document.first_indexed_at),
                        _format_timestamp(document.last_indexed_at),
                    ),
                )

            for document in updated:
                cursor.execute(
                    _UPDATE_DOCUMENT,
                    (
                        document.content_hash,
                        document.file_size,
                        _format_timestamp(document.modified_at),
                        _format_timestamp(document.last_indexed_at),
                        document.relative_path.as_posix(),
                    ),
                )
                self._require_one_row(cursor, "update", document.relative_path)

            for relative_path in deleted:
                cursor.execute(_DELETE_DOCUMENT, (relative_path.as_posix(),))
                self._require_one_row(cursor, "delete", relative_path)

            cursor.execute(
                _UPSERT_METADATA,
                (LAST_INDEXED_AT_KEY, _format_timestamp(indexed_at)),
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run statements inside one explicit transaction.

        Any failure rolls the whole transaction back so the index is never
        left partially updated.
        """
        cursor = self._connection.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")

            try:
                yield cursor
            except BaseException:
                self._connection.rollback()
                raise

            self._connection.commit()
        except sqlite3.Error as exc:
            raise IndexDatabaseError(
                f"Index database update failed for {self.database_path}: {exc}"
            ) from exc
        finally:
            cursor.close()

    @contextmanager
    def _read_cursor(self) -> Iterator[sqlite3.Cursor]:
        cursor = self._connection.cursor()

        try:
            yield cursor
        except sqlite3.Error as exc:
            raise IndexDatabaseError(
                f"Could not read index database {self.database_path}: {exc}"
            ) from exc
        finally:
            cursor.close()

    def _require_one_row(
        self,
        cursor: sqlite3.Cursor,
        action: str,
        relative_path: Path,
    ) -> None:
        if cursor.rowcount == 1:
            return

        raise IndexDatabaseError(
            f"Index {action} affected {cursor.rowcount} record(s) for "
            f"{relative_path.as_posix()}; expected exactly one"
        )

    def _row_to_document(
        self,
        row: tuple[str, str, int, str, str, str],
    ) -> IndexedDocument:
        return IndexedDocument(
            relative_path=Path(row[0]),
            content_hash=row[1],
            file_size=int(row[2]),
            modified_at=self._parse_timestamp(row[3]),
            first_indexed_at=self._parse_timestamp(row[4]),
            last_indexed_at=self._parse_timestamp(row[5]),
        )

    def _parse_timestamp(self, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise IndexDatabaseError(
                f"Index database {self.database_path} contains an invalid "
                "timestamp. Rebuild it with: journal-ai index --rebuild"
            ) from exc

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)

        return parsed.astimezone(UTC)


@contextmanager
def open_index_database(database_path: Path) -> Iterator[IndexDatabase]:
    """Open the index database, creating its directory and schema if needed."""
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IndexDatabaseError(
            f"Could not create index directory: {database_path.parent}"
        ) from exc

    try:
        connection = sqlite3.connect(database_path, isolation_level=None)
    except sqlite3.Error as exc:
        raise IndexDatabaseError(
            f"Could not open index database {database_path}: {exc}"
        ) from exc

    try:
        database = IndexDatabase(connection, database_path)
        database.initialize()
        yield database
    finally:
        connection.close()


def delete_index_database(database_path: Path) -> None:
    """Remove the index database file so it can be rebuilt from source.

    Only the database file and its SQLite sidecar files are removed. Journal
    entries are never touched.
    """
    for candidate in (
        database_path,
        *(
            database_path.with_name(database_path.name + suffix)
            for suffix in DATABASE_SIDECAR_SUFFIXES
        ),
    ):
        if not candidate.exists():
            continue

        if not candidate.is_file():
            raise IndexDatabaseError(
                f"Refusing to delete non-file index path: {candidate}"
            )

        try:
            candidate.unlink()
        except OSError as exc:
            raise IndexDatabaseError(
                f"Could not delete index database file: {candidate}"
            ) from exc


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()

    return value.astimezone(UTC).isoformat()


def _fetch_optional_text(
    cursor: sqlite3.Cursor,
    statement: str,
    parameters: tuple[object, ...],
) -> str | None:
    row = cursor.execute(statement, parameters).fetchone()

    if row is None:
        return None

    return str(row[0])
