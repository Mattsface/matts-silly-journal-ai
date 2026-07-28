from __future__ import annotations

import argparse
from pathlib import Path

from journal_ai.journal_reader import (
    JournalError,
    load_journal_documents,
    read_markdown_file,
)
from journal_ai.ollama_client import OllamaClient, OllamaError
from journal_ai.prompts import (
    SYSTEM_PROMPT,
    build_entry_analysis_prompt,
)

DEFAULT_JOURNAL_PATH = Path.home() / "journal"
DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="journal-ai",
        description="Local tools for an encrypted Markdown journal.",
    )

    parser.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help=f"Mounted journal directory. Default: {DEFAULT_JOURNAL_PATH}",
    )

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

    analyze_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model to use. Default: {DEFAULT_MODEL}",
    )

    analyze_parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help=f"Ollama server URL. Default: {DEFAULT_OLLAMA_URL}",
    )

    return parser


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


def analyze_document(
    *,
    journal_path: Path,
    relative_file: Path,
    model: str,
    ollama_url: str,
) -> int:
    resolved_journal_path = journal_path.expanduser().resolve()
    selected_path = resolved_journal_path / relative_file

    document = read_markdown_file(
        selected_path,
        resolved_journal_path,
    )

    client = OllamaClient(base_url=ollama_url)

    response = client.generate(
        model=model,
        system=SYSTEM_PROMPT,
        prompt=build_entry_analysis_prompt(document),
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

    return 0


def main() -> int:
    args = build_parser().parse_args()
    command = args.command or "list"

    try:
        if command == "list":
            return list_documents(args.journal_path)

        if command == "analyze":
            return analyze_document(
                journal_path=args.journal_path,
                relative_file=args.file,
                model=args.model,
                ollama_url=args.ollama_url,
            )
    except (JournalError, OllamaError) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Error: Unknown command: {command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
