from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from journal_ai.models import DocumentChunk, IndexedDocument, TextChunk

SCHEMA_VERSION = 2

SCHEMA_VERSION_KEY = "schema_version"
LAST_INDEXED_AT_KEY = "last_indexed_at"
CHUNKING_SIGNATURE_KEY = "chunking_signature"

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

_CREATE_CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    character_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (document_id)
        REFERENCES documents(id)
        ON DELETE CASCADE,
    UNIQUE(document_id, chunk_index)
)
"""

_SELECT_DOCUMENTS = """
SELECT
    id,
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

_SELECT_DOCUMENT_ID = "SELECT id FROM documents WHERE relative_path = ?"

_DELETE_CHUNKS_FOR_DOCUMENT = "DELETE FROM chunks WHERE document_id = ?"

_INSERT_CHUNK = """
INSERT INTO chunks (
    document_id,
    chunk_index,
    content,
    content_hash,
    start_line,
    end_line,
    character_count,
    created_at,
    updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_CHUNKS_FOR_DOCUMENT = """
SELECT
    document_id,
    chunk_index,
    content,
    content_hash,
    start_line,
    end_line,
    character_count
FROM chunks
WHERE document_id = ?
ORDER BY chunk_index
"""

_COUNT_CHUNKS = "SELECT COUNT(*) FROM chunks"

_UPSERT_METADATA = """
INSERT INTO index_metadata (key, value) VALUES (?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""

_SELECT_METADATA = "SELECT value FROM index_metadata WHERE key = ?"


@dataclass(frozen=True, slots=True)
class ChunkChangeCounts:
    """How many chunk rows were added or removed in one index transaction."""

    created: int
    removed: int


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
                    "required. Rebuild it with: "
                    "uv run journal-ai index --rebuild"
                )

            cursor.execute(_CREATE_DOCUMENTS_TABLE)
            cursor.execute(_CREATE_CHUNKS_TABLE)

    def read_documents(self) -> dict[Path, IndexedDocument]:
        """Return every indexed document keyed by its relative path."""
        with self._read_cursor() as cursor:
            rows = cursor.execute(_SELECT_DOCUMENTS).fetchall()

        documents = (self._row_to_document(row) for row in rows)
        return {document.relative_path: document for document in documents}

    def read_document_ids(self) -> dict[Path, int]:
        """Return database ids for every indexed document."""
        with self._read_cursor() as cursor:
            rows = cursor.execute(
                "SELECT id, relative_path FROM documents ORDER BY relative_path"
            ).fetchall()

        return {Path(row[1]): int(row[0]) for row in rows}

    def document_count(self) -> int:
        """Return how many documents the index currently tracks."""
        with self._read_cursor() as cursor:
            row = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()

        return int(row[0])

    def chunk_count(self) -> int:
        """Return how many chunk rows the index currently stores."""
        with self._read_cursor() as cursor:
            row = cursor.execute(_COUNT_CHUNKS).fetchone()

        return int(row[0])

    def read_chunks_for_document(self, document_id: int) -> tuple[DocumentChunk, ...]:
        """Return stored chunks for one document, ordered by chunk_index."""
        with self._read_cursor() as cursor:
            rows = cursor.execute(
                _SELECT_CHUNKS_FOR_DOCUMENT,
                (document_id,),
            ).fetchall()

        return tuple(self._row_to_chunk(row) for row in rows)

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

    def read_chunking_signature(self) -> str | None:
        """Return the stored chunking-configuration signature, if any."""
        with self._read_cursor() as cursor:
            return _fetch_optional_text(
                cursor,
                _SELECT_METADATA,
                (CHUNKING_SIGNATURE_KEY,),
            )

    def apply_changes(
        self,
        *,
        indexed_at: datetime,
        inserted: Sequence[IndexedDocument] = (),
        updated: Sequence[IndexedDocument] = (),
        deleted: Sequence[Path] = (),
        chunks_by_path: Mapping[Path, tuple[TextChunk, ...]] | None = None,
        chunking_signature: str | None = None,
    ) -> ChunkChangeCounts:
        """Apply one prepared set of index changes in a single transaction."""
        chunk_map = chunks_by_path or {}
        created = 0
        removed = 0
        timestamp = _format_timestamp(indexed_at)
        handled_chunk_paths: set[Path] = set()

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
                row_id = cursor.lastrowid
                if row_id is None:
                    raise IndexDatabaseError(
                        "Insert did not return a document id for "
                        f"{document.relative_path.as_posix()}"
                    )
                document_id = int(row_id)
                new_chunks = chunk_map.get(document.relative_path, ())
                created += self._replace_chunks(
                    cursor,
                    document_id=document_id,
                    chunks=new_chunks,
                    timestamp=timestamp,
                )
                handled_chunk_paths.add(document.relative_path)

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

                if document.relative_path in chunk_map:
                    document_id = self._require_document_id(
                        cursor,
                        document.relative_path,
                    )
                    removed += self._count_chunks(cursor, document_id)
                    new_chunks = chunk_map[document.relative_path]
                    created += self._replace_chunks(
                        cursor,
                        document_id=document_id,
                        chunks=new_chunks,
                        timestamp=timestamp,
                    )
                    handled_chunk_paths.add(document.relative_path)

            for relative_path, new_chunks in chunk_map.items():
                if relative_path in handled_chunk_paths:
                    continue

                document_id = self._require_document_id(cursor, relative_path)
                removed += self._count_chunks(cursor, document_id)
                created += self._replace_chunks(
                    cursor,
                    document_id=document_id,
                    chunks=new_chunks,
                    timestamp=timestamp,
                )

            for relative_path in deleted:
                deleted_document_id = self._optional_document_id(
                    cursor,
                    relative_path,
                )
                if deleted_document_id is not None:
                    removed += self._count_chunks(cursor, deleted_document_id)

                cursor.execute(_DELETE_DOCUMENT, (relative_path.as_posix(),))
                self._require_one_row(cursor, "delete", relative_path)

            cursor.execute(
                _UPSERT_METADATA,
                (LAST_INDEXED_AT_KEY, timestamp),
            )

            if chunking_signature is not None:
                cursor.execute(
                    _UPSERT_METADATA,
                    (CHUNKING_SIGNATURE_KEY, chunking_signature),
                )

        return ChunkChangeCounts(created=created, removed=removed)

    def _replace_chunks(
        self,
        cursor: sqlite3.Cursor,
        *,
        document_id: int,
        chunks: Sequence[TextChunk],
        timestamp: str,
    ) -> int:
        cursor.execute(_DELETE_CHUNKS_FOR_DOCUMENT, (document_id,))

        for chunk in chunks:
            cursor.execute(
                _INSERT_CHUNK,
                (
                    document_id,
                    chunk.chunk_index,
                    chunk.content,
                    chunk.content_hash,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.character_count,
                    timestamp,
                    timestamp,
                ),
            )

        return len(chunks)

    def _count_chunks(self, cursor: sqlite3.Cursor, document_id: int) -> int:
        row = cursor.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return int(row[0])

    def _require_document_id(
        self,
        cursor: sqlite3.Cursor,
        relative_path: Path,
    ) -> int:
        document_id = self._optional_document_id(cursor, relative_path)
        if document_id is None:
            raise IndexDatabaseError(
                f"Missing document id for {relative_path.as_posix()}"
            )

        return document_id

    def _optional_document_id(
        self,
        cursor: sqlite3.Cursor,
        relative_path: Path,
    ) -> int | None:
        row = cursor.execute(
            _SELECT_DOCUMENT_ID,
            (relative_path.as_posix(),),
        ).fetchone()

        if row is None:
            return None

        return int(row[0])

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
        row: tuple[int, str, str, int, str, str, str],
    ) -> IndexedDocument:
        return IndexedDocument(
            relative_path=Path(row[1]),
            content_hash=row[2],
            file_size=int(row[3]),
            modified_at=self._parse_timestamp(row[4]),
            first_indexed_at=self._parse_timestamp(row[5]),
            last_indexed_at=self._parse_timestamp(row[6]),
        )

    def _row_to_chunk(
        self,
        row: tuple[int, int, str, str, int, int, int],
    ) -> DocumentChunk:
        return DocumentChunk(
            document_id=int(row[0]),
            chunk_index=int(row[1]),
            content=row[2],
            content_hash=row[3],
            start_line=int(row[4]),
            end_line=int(row[5]),
            character_count=int(row[6]),
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

    connection.execute("PRAGMA foreign_keys = ON")

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
