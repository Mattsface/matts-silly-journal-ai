from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from journal_ai.models import JournalDocument

EXCLUDED_TOP_LEVEL_DIRS = frozenset({"generated", ".journal-ai"})


class JournalError(Exception):
    """Base exception for journal access problems."""


class JournalNotMountedError(JournalError):
    """Raised when the decrypted journal directory is not mounted."""


class JournalReadError(JournalError):
    """Raised when a journal file cannot be read safely."""


def is_mountpoint(path: Path) -> bool:
    """Return True when path is an active filesystem mount point."""
    return path.is_mount()


def _is_excluded_source_path(
    file_path: Path,
    journal_path: Path,
) -> bool:
    """Return True when a Markdown path must not be treated as source."""
    try:
        relative_path = file_path.relative_to(journal_path)
    except ValueError:
        return True

    return bool(
        relative_path.parts
        and relative_path.parts[0] in EXCLUDED_TOP_LEVEL_DIRS
    )


def find_markdown_files(journal_path: Path) -> list[Path]:
    """Return all Markdown files inside the mounted journal."""
    journal_path = journal_path.expanduser().resolve()

    if not journal_path.exists():
        raise JournalError(f"Journal path does not exist: {journal_path}")

    if not journal_path.is_dir():
        raise JournalError(f"Journal path is not a directory: {journal_path}")

    if not is_mountpoint(journal_path):
        raise JournalNotMountedError(
            f"Journal is not mounted: {journal_path}\n"
            "Unlock it with:\n"
            f"  gocryptfs ~/journal-encrypted {journal_path}"
        )

    return sorted(
        path
        for path in journal_path.rglob("*.md")
        if path.is_file()
        and not _is_excluded_source_path(path, journal_path)
    )


def read_markdown_file(
    file_path: Path,
    journal_path: Path,
) -> JournalDocument:
    """Read one Markdown file into an immutable document object."""
    resolved_file_path = file_path.expanduser().resolve()
    resolved_journal_path = journal_path.expanduser().resolve()

    try:
        relative_path = resolved_file_path.relative_to(resolved_journal_path)
    except ValueError as exc:
        raise JournalReadError(
            f"File is outside the journal directory: {resolved_file_path}"
        ) from exc

    if not resolved_file_path.is_file():
        raise JournalReadError(
            f"Journal file does not exist: {resolved_file_path}"
        )

    try:
        content = resolved_file_path.read_text(encoding="utf-8")
        modified_at = datetime.fromtimestamp(
            resolved_file_path.stat().st_mtime,
            tz=UTC,
        )
    except (OSError, UnicodeError) as exc:
        raise JournalReadError(
            f"Could not read journal file: {resolved_file_path}"
        ) from exc

    return JournalDocument(
        path=resolved_file_path,
        relative_path=relative_path,
        content=content,
        modified_at=modified_at,
    )


def load_journal_documents(
    journal_path: Path,
) -> list[JournalDocument]:
    """Load all Markdown files from the mounted journal."""
    resolved_journal_path = journal_path.expanduser().resolve()
    markdown_files = find_markdown_files(resolved_journal_path)

    return [
        read_markdown_file(file_path, resolved_journal_path)
        for file_path in markdown_files
    ]
