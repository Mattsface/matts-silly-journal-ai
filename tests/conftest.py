from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point the home directory at a temporary directory.

    This keeps tests away from the real journal and the real
    ~/.config/journal-ai/config.toml file.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


@pytest.fixture
def mounted_journal(tmp_path: Path) -> Iterator[Path]:
    """Provide a temporary journal directory that looks like a live mount.

    Mount detection is mocked so tests never need the real encrypted volume.
    """
    journal_path = tmp_path / "journal"
    (journal_path / "journals").mkdir(parents=True)

    with patch("journal_ai.journal_reader.is_mountpoint", return_value=True):
        yield journal_path
