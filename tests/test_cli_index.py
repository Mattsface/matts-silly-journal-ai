from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from journal_ai.cli import build_parser, main
from journal_ai.config import index_database_path


def write_entry(journal_path: Path, relative_path: str, content: str) -> Path:
    file_path = journal_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def run_index(journal_path: Path, *extra_argv: str) -> int:
    return main(["--journal-path", str(journal_path), "index", *extra_argv])


def test_index_command_reports_new_files(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/first.md", "First entry.")
    write_entry(mounted_journal, "journals/second.md", "Second entry.")

    exit_code = run_index(mounted_journal)

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Index updated." in captured
    assert "New:       2" in captured
    assert "Changed:   0" in captured
    assert "Unchanged: 0" in captured
    assert "Deleted:   0" in captured
    assert "Chunks" in captured
    assert "Total:" in captured


def test_second_index_run_reports_no_changes(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/first.md", "First entry.")
    write_entry(mounted_journal, "journals/second.md", "Second entry.")
    assert run_index(mounted_journal) == 0
    capsys.readouterr()

    exit_code = run_index(mounted_journal)

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Index already current." in captured
    assert "New:       0" in captured
    assert "Changed:   0" in captured
    assert "Unchanged: 2" in captured
    assert "Deleted:   0" in captured
    assert "Chunks" in captured
    assert "Total:" in captured


def test_index_command_never_prints_journal_content(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "A private sentence that must stay in the journal."
    write_entry(mounted_journal, "journals/entry.md", secret)

    assert run_index(mounted_journal) == 0
    assert run_index(mounted_journal, "--status") == 0
    assert run_index(mounted_journal, "--rebuild") == 0

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "journals/entry.md" not in captured.out


def test_index_status_reports_metadata(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    assert run_index(mounted_journal) == 0
    capsys.readouterr()

    exit_code = run_index(mounted_journal, "--status")

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Database: {index_database_path(mounted_journal)}"
    assert lines[1] == "Documents: 1"
    assert lines[2] == "Chunks: 1"
    assert lines[3].startswith("Last updated: 20")
    assert lines[3].endswith("+00:00")


def test_index_status_without_a_database_creates_nothing(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    exit_code = run_index(mounted_journal, "--status")

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Documents: 0" in captured
    assert "Last updated: never" in captured
    assert "journal-ai index" in captured
    assert not index_database_path(mounted_journal).exists()


def test_index_rebuild_recreates_the_database(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/first.md", "First entry.")
    removed_path = write_entry(
        mounted_journal,
        "journals/second.md",
        "Second entry.",
    )
    assert run_index(mounted_journal) == 0
    removed_path.unlink()
    capsys.readouterr()

    exit_code = run_index(mounted_journal, "--rebuild")

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Index rebuilt." in captured
    assert "New:       1" in captured
    assert "Unchanged: 0" in captured
    assert index_database_path(mounted_journal).is_file()


def test_index_leaves_source_files_unchanged(mounted_journal: Path) -> None:
    original = "Today was calm."
    file_path = write_entry(mounted_journal, "journals/entry.md", original)
    before = file_path.stat().st_mtime_ns

    assert run_index(mounted_journal) == 0
    assert run_index(mounted_journal, "--rebuild") == 0

    assert file_path.read_text(encoding="utf-8") == original
    assert file_path.stat().st_mtime_ns == before


def test_index_uses_the_configured_journal_path(
    mounted_journal: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'journal_path = "{mounted_journal}"\n',
        encoding="utf-8",
    )

    exit_code = main(["--config", str(config_path), "index"])

    assert exit_code == 0
    assert "New:       1" in capsys.readouterr().out
    assert index_database_path(mounted_journal).is_file()


def test_unmounted_journal_reports_a_clear_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    exit_code = run_index(journal_path)

    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "Journal is not mounted" in captured
    assert not (journal_path / ".journal-ai").exists()


def test_unmounted_journal_status_reports_a_clear_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    exit_code = run_index(journal_path, "--status")

    assert exit_code == 1
    assert "Journal is not mounted" in capsys.readouterr().out


def test_missing_journal_reports_a_clear_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_index(tmp_path / "missing")

    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().out


def test_corrupt_database_reports_a_clear_error(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    database_path = index_database_path(mounted_journal)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.write_bytes(b"this is not a database")

    exit_code = run_index(mounted_journal)

    assert exit_code == 1
    assert "Error:" in capsys.readouterr().out


def test_status_and_rebuild_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["index", "--status", "--rebuild"])


def test_index_subcommand_accepts_its_own_journal_path(
    mounted_journal: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    exit_code = main(["index", "--journal-path", str(mounted_journal)])

    assert exit_code == 0
    assert "New:       1" in capsys.readouterr().out


@patch("journal_ai.ollama_client.urlopen")
def test_index_command_makes_no_requests(
    mock_urlopen: MagicMock,
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    assert run_index(mounted_journal) == 0
    assert run_index(mounted_journal, "--status") == 0
    assert run_index(mounted_journal, "--rebuild") == 0

    mock_urlopen.assert_not_called()
