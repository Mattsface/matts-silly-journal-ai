from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

GENERATED_ANALYSES_DIR = Path("generated") / "analyses"


class OutputWriteError(Exception):
    """Raised when generated analysis output cannot be written safely."""


def save_analysis(
    *,
    journal_path: Path,
    source_relative_path: Path,
    model: str,
    analysis_text: str,
    generated_at: datetime | None = None,
) -> Path:
    """Write one analysis under generated/analyses without touching sources."""
    stripped_analysis = analysis_text.strip()
    if not stripped_analysis:
        raise OutputWriteError(
            "Refusing to save an empty or whitespace-only analysis"
        )

    resolved_journal_path = journal_path.expanduser().resolve()
    if not resolved_journal_path.is_dir():
        raise OutputWriteError(
            f"Journal path is not a directory: {resolved_journal_path}"
        )

    timestamp = generated_at or datetime.now(tz=UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)

    output_dir = resolved_journal_path / GENERATED_ANALYSES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{source_relative_path.stem}.md"
    )
    output_path = output_dir / filename

    try:
        output_path.relative_to(resolved_journal_path / "generated")
    except ValueError as exc:
        raise OutputWriteError(
            f"Analysis path is outside generated output: {output_path}"
        ) from exc

    content = (
        "# Journal analysis\n"
        f"Source: `{source_relative_path.as_posix()}`\n"
        f"Model: `{model}`\n"
        f"Generated: `{timestamp.isoformat()}`\n"
        "---\n"
        "\n"
        f"{stripped_analysis}\n"
    )

    try:
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(
            f"Could not write analysis file: {output_path}"
        ) from exc

    return output_path
