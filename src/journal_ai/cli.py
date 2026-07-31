from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from journal_ai.config import (
    DEFAULT_CONFIG_LOCATION,
    DEFAULT_JOURNAL_LOCATION,
    AppConfig,
    ConfigError,
    OllamaConfig,
    apply_overrides,
    load_config,
)
from journal_ai.hashing import HashingError
from journal_ai.index_database import IndexDatabaseError
from journal_ai.index_service import (
    read_index_status,
    rebuild_index,
    update_index,
)
from journal_ai.journal_reader import (
    JournalError,
    load_journal_documents,
    read_markdown_file,
)
from journal_ai.ollama_client import OllamaClient, OllamaError
from journal_ai.output_writer import OutputWriteError, save_analysis
from journal_ai.prompts import (
    SYSTEM_PROMPT,
    build_entry_analysis_prompt,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="journal-ai",
        description="Local tools for an encrypted Markdown journal.",
    )

    _add_settings_arguments(parser, suppress_defaults=False)

    subparsers = parser.add_subparsers(
        dest="command",
        required=False,
    )

    subparsers.add_parser(
        "list",
        help="List available Markdown journal files.",
    )

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze one explicitly selected journal file.",
    )

    analyze_parser.add_argument(
        "file",
        type=Path,
        help="Path relative to the mounted journal directory.",
    )

    # The same settings are accepted before or after the subcommand.
    # Suppressed defaults keep unused options from clearing values that
    # were already supplied to the top-level parser.
    _add_settings_arguments(analyze_parser, suppress_defaults=True)

    analyze_parser.add_argument(
        "--save",
        action="store_true",
        help="Save the analysis under generated/analyses.",
    )

    index_parser = subparsers.add_parser(
        "index",
        help="Update the local index of journal files.",
    )

    index_mode = index_parser.add_mutually_exclusive_group()

    index_mode.add_argument(
        "--status",
        action="store_true",
        help="Show index metadata without scanning the journal.",
    )

    index_mode.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete the index and rebuild it from current source files.",
    )

    # Indexing never contacts Ollama, so only the journal options apply.
    _add_journal_arguments(index_parser, suppress_defaults=True)

    return parser


def _add_settings_arguments(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
) -> None:
    """Add the options that override configuration-file values."""
    default: Any = argparse.SUPPRESS if suppress_defaults else None
    defaults = OllamaConfig()

    _add_journal_arguments(parser, suppress_defaults=suppress_defaults)

    parser.add_argument(
        "--model",
        default=default,
        help=f"Ollama model to use. Default: {defaults.model}",
    )

    parser.add_argument(
        "--ollama-url",
        default=default,
        help=f"Ollama server URL. Default: {defaults.url}",
    )

    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=default,
        help=(
            "Ollama request timeout in seconds. "
            f"Default: {defaults.timeout_seconds}"
        ),
    )

    parser.add_argument(
        "--num-predict",
        type=int,
        default=default,
        help=(
            "Maximum number of generated tokens. "
            f"Default: {defaults.num_predict}"
        ),
    )

    thinking = parser.add_mutually_exclusive_group()

    thinking.add_argument(
        "--think",
        dest="think",
        action="store_true",
        default=default,
        help="Ask the model to think before answering.",
    )

    thinking.add_argument(
        "--no-think",
        dest="think",
        action="store_false",
        default=default,
        help="Disable model thinking.",
    )


def _add_journal_arguments(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
) -> None:
    """Add the options that select the configuration file and journal."""
    default: Any = argparse.SUPPRESS if suppress_defaults else None

    parser.add_argument(
        "--config",
        type=Path,
        default=default,
        help=(
            "Configuration file to read. "
            f"Default: {DEFAULT_CONFIG_LOCATION}"
        ),
    )

    parser.add_argument(
        "--journal-path",
        type=Path,
        default=default,
        help=f"Mounted journal directory. Default: {DEFAULT_JOURNAL_LOCATION}",
    )


def resolve_config(args: argparse.Namespace) -> AppConfig:
    """Resolve settings from CLI options, the config file, and defaults."""
    return apply_overrides(
        load_config(args.config),
        journal_path=args.journal_path,
        ollama_url=args.ollama_url,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
        num_predict=args.num_predict,
        think=args.think,
    )


def list_documents(journal_path: Path) -> int:
    documents = load_journal_documents(journal_path)

    if not documents:
        print(f"No Markdown files found in {journal_path}")
        return 0

    print(f"Loaded {len(documents)} Markdown document(s):")

    for document in documents:
        print(
            f"  {document.relative_path} "
            f"({document.word_count} words, "
            f"{document.character_count} characters)"
        )

    return 0


def index_journal(*, config: AppConfig, rebuild: bool = False) -> int:
    """Update or rebuild the local index and print a summary of the changes."""
    if rebuild:
        result = rebuild_index(journal_path=config.journal_path)
        summary = "Index rebuilt."
    else:
        result = update_index(journal_path=config.journal_path)
        summary = (
            "Index updated."
            if result.has_changes
            else "Index already current."
        )

    print(summary)
    print(f"{'New:':<11}{result.new}")
    print(f"{'Changed:':<11}{result.changed}")
    print(f"{'Unchanged:':<11}{result.unchanged}")
    print(f"{'Deleted:':<11}{result.deleted}")

    return 0


def show_index_status(*, config: AppConfig) -> int:
    """Print index metadata without scanning the journal."""
    status = read_index_status(journal_path=config.journal_path)

    last_indexed_at = "never"
    if status.last_indexed_at is not None:
        last_indexed_at = status.last_indexed_at.isoformat(timespec="seconds")

    print(f"Database: {status.database_path}")
    print(f"Documents: {status.document_count}")
    print(f"Last updated: {last_indexed_at}")

    if not status.database_exists:
        print()
        print("No index database yet. Create it with: journal-ai index")

    return 0


def analyze_document(
    *,
    config: AppConfig,
    relative_file: Path,
    save: bool = False,
) -> int:
    resolved_journal_path = config.journal_path.expanduser().resolve()
    selected_path = resolved_journal_path / relative_file

    document = read_markdown_file(
        selected_path,
        resolved_journal_path,
    )

    client = OllamaClient(
        base_url=config.ollama.url,
        timeout_seconds=config.ollama.timeout_seconds,
    )

    response = client.generate(
        model=config.ollama.model,
        system=SYSTEM_PROMPT,
        prompt=build_entry_analysis_prompt(document),
        num_predict=config.ollama.num_predict,
        think=config.ollama.think,
    )

    print(f"Source: {document.relative_path}")
    print(f"Model: {response.model}")
    print()
    print(response.text)

    if response.prompt_tokens is not None:
        print()
        print(
            "Usage: "
            f"{response.prompt_tokens} input tokens, "
            f"{response.response_tokens or 0} output tokens"
        )

    if save:
        output_path = save_analysis(
            journal_path=resolved_journal_path,
            source_relative_path=document.relative_path,
            model=response.model,
            analysis_text=response.text,
        )
        print()
        print(f"Saved analysis to: {output_path}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "list"

    try:
        config = resolve_config(args)

        if command == "list":
            return list_documents(config.journal_path)

        if command == "analyze":
            return analyze_document(
                config=config,
                relative_file=args.file,
                save=args.save,
            )

        if command == "index":
            if args.status:
                return show_index_status(config=config)

            return index_journal(config=config, rebuild=args.rebuild)
    except (
        ConfigError,
        HashingError,
        IndexDatabaseError,
        JournalError,
        OllamaError,
        OutputWriteError,
    ) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Error: Unknown command: {command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
