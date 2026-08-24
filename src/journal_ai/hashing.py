from __future__ import annotations

import hashlib
from pathlib import Path

HASH_ALGORITHM = "sha256"
DEFAULT_CHUNK_SIZE = 65536


class HashingError(Exception):
    """Raised when a file cannot be read to calculate its content hash."""


def hash_file(
    file_path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Return the SHA-256 hex digest of one file's bytes.

    The file is read in chunks so large entries never need to be held in
    memory, and the bytes are hashed rather than decoded text so the digest
    stays stable regardless of encoding details.
    """
    digest = hashlib.new(HASH_ALGORITHM)

    try:
        with file_path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise HashingError(
            f"Could not read file to calculate its hash: {file_path}"
        ) from exc

    return digest.hexdigest()


def hash_text(content: str) -> str:
    """Return the SHA-256 hex digest of a Unicode string's UTF-8 bytes."""
    return hashlib.new(HASH_ALGORITHM, content.encode("utf-8")).hexdigest()
