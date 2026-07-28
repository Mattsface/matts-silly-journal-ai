from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class JournalDocument:
    """A read-only representation of one Markdown journal file."""

    path: Path
    relative_path: Path
    content: str
    modified_at: datetime

    @property
    def character_count(self) -> int:
        return len(self.content)

    @property
    def word_count(self) -> int:
        return len(self.content.split())
