# journal-ai

> **Work in progress:** This project is under active development. Features, commands, configuration, and architecture may change.

A private, local journal assistant for encrypted Markdown journals.

`journal-ai` reads journal entries stored in a mounted `gocryptfs` volume, sends only explicitly selected entries to an Ollama model, and stores generated analyses separately from the original journal files.

## Goals

* Keep journal entries local and encrypted
* Store journal entries as plain Markdown
* Never modify original journal entries
* Send only explicitly selected content to the LLM
* Support local or private-network Ollama inference
* Keep AI-generated output separate from human-written entries
* Eventually support semantic search, weekly reviews, and pattern analysis
* Back up only encrypted journal data to the cloud

## Architecture

```text
Logseq
   |
   v
~/journal
Decrypted gocryptfs mount
   |
   v
journal-ai
   |
   v
Ollama
   |
   v
~/journal/generated/analyses
```

The encrypted source data is stored in:

```text
~/journal-encrypted
```

The readable journal is available only while the encrypted volume is mounted:

```text
~/journal
```

## Project Status

The current implementation supports:

* Detecting whether the encrypted journal is mounted
* Discovering Markdown files inside the journal
* Excluding AI-generated files from source discovery
* Loading journal entries into immutable Python objects
* Rejecting files outside the journal directory
* Listing journal entries with word and character counts
* Analyzing one explicitly selected journal entry
* Connecting to a local or remote Ollama server
* Saving generated analyses under `generated/analyses`
* Preserving original journal files unchanged
* Automated tests, linting, and type checking

Planned features are documented later in this README.

## Project Structure

```text
journal-ai/
├── src/
│   └── journal_ai/
│       ├── __init__.py
│       ├── cli.py
│       ├── journal_reader.py
│       ├── models.py
│       ├── ollama_client.py
│       ├── output_writer.py
│       └── prompts.py
├── tests/
│   ├── test_journal_reader.py
│   ├── test_ollama_client.py
│   └── test_output_writer.py
├── docs/
├── scripts/
├── pyproject.toml
└── README.md
```

## Requirements

* Ubuntu or another Linux distribution
* Python 3.12 or newer
* `uv`
* `gocryptfs`
* Ollama
* A Markdown journal such as a Logseq graph
* `zenity` for the graphical unlock prompt

## Installation

Clone the repository and enter the project directory:

```bash
git clone <repository-url>
cd journal-ai
```

Install the project dependencies:

```bash
uv sync
```

Run the development checks:

```bash
uv run ruff check .
uv run pytest
uv run mypy src
```

## Journal Setup

The expected directory layout is:

```text
~/journal-encrypted/    Encrypted gocryptfs data
~/journal/              Decrypted mount used by Logseq
```

Unlock the journal manually:

```bash
gocryptfs "$HOME/journal-encrypted" "$HOME/journal"
```

Lock the journal:

```bash
fusermount3 -u "$HOME/journal"
```

The application refuses to read the journal when `~/journal` is not an active mount point.

## i3 Unlock and Lock Commands

The helper commands are stored under:

```text
~/.local/bin/
```

The commands are:

```text
journal-unlock
journal-lock
```

### Graphical unlock command

The unlock script uses Zenity to display a graphical password prompt:

```bash
#!/usr/bin/env bash
set -euo pipefail

ENCRYPTED_DIR="$HOME/journal-encrypted"
MOUNT_DIR="$HOME/journal"

mkdir -p "$ENCRYPTED_DIR" "$MOUNT_DIR"

if mountpoint -q "$MOUNT_DIR"; then
    notify-send "Journal" "The journal is already unlocked."
    exit 0
fi

if gocryptfs \
    -extpass "zenity --password --title=Unlock journal" \
    "$ENCRYPTED_DIR" \
    "$MOUNT_DIR"
then
    notify-send "Journal unlocked" "The journal is now available."
else
    zenity \
        --error \
        --title="Journal error" \
        --text="The password was rejected or the journal could not be mounted."
    exit 1
fi
```

### Lock command

```bash
#!/usr/bin/env bash
set -euo pipefail

MOUNT_DIR="$HOME/journal"

if ! mountpoint -q "$MOUNT_DIR"; then
    notify-send "Journal" "The journal is already locked."
    exit 0
fi

pkill -TERM -f 'com.logseq.Logseq' 2>/dev/null || true
sleep 2

if fusermount3 -u "$MOUNT_DIR"; then
    notify-send "Journal locked" "The encrypted journal has been unmounted."
else
    notify-send \
        -u critical \
        "Journal still open" \
        "Close programs using $MOUNT_DIR and try again."
    exit 1
fi
```

Make both scripts executable:

```bash
chmod +x "$HOME/.local/bin/journal-unlock"
chmod +x "$HOME/.local/bin/journal-lock"
```

Example i3 bindings:

```text
bindsym $mod+Shift+j exec --no-startup-id journal-unlock
bindsym $mod+Shift+k exec --no-startup-id journal-lock
```

Reload i3 after changing its configuration:

```text
$mod+Shift+r
```

## Usage

### List journal entries

```bash
uv run journal-ai
```

Or:

```bash
uv run journal-ai list
```

Example output:

```text
Loaded 1 Markdown document(s):
  journals/2026_07_28.md (120 words, 694 characters)
```

### Analyze one entry

Use the relative path shown by the list command:

```bash
uv run journal-ai analyze journals/2026_07_28.md
```

Only the explicitly selected file is sent to Ollama.

### Save an analysis

```bash
uv run journal-ai analyze journals/2026_07_28.md --save
```

Generated output is stored under:

```text
~/journal/generated/analyses/
```

Generated analyses are excluded from source discovery so the model does not analyze its own output.

### Use a different Ollama model

```bash
uv run journal-ai analyze journals/2026_07_28.md \
  --model qwen3.5:4b
```

### Use a remote Ollama server

```bash
uv run journal-ai analyze journals/2026_07_28.md \
  --ollama-url http://192.168.1.42:11434
```

This allows Ollama to run on another computer, such as a MacBook M1 Pro acting as a private local inference server.

Do not expose an unauthenticated Ollama server directly to the public internet.

## Ollama Configuration

The current default model is:

```text
qwen3.5:4b
```

Generation is intentionally limited:

```python
"options": {
    "num_predict": 300,
}
```

Limiting output tokens reduces response time on slower hardware and keeps single-entry analyses concise.

Thinking is disabled when supported:

```python
"think": False
```

This prevents the model from spending the output-token allowance on hidden reasoning without producing a visible answer.

## Security Model

The project currently follows these rules:

1. Journal entries remain inside an encrypted `gocryptfs` volume.
2. The application reads Markdown only from the mounted journal.
3. Files outside the journal directory are rejected.
4. Original journal entries are never modified.
5. Generated analyses are written to a separate directory.
6. Only explicitly selected entries are sent to Ollama.
7. The journal itself is not stored in the Git repository.
8. Cloud backups must contain only encrypted journal data.
9. The decrypted mount must never be synchronized to cloud storage.
10. Ollama should run locally or on a trusted private network.

The directory intended for encrypted cloud backup is:

```text
~/journal-encrypted
```

The decrypted mount must never be uploaded:

```text
~/journal
```

## Development Commands

Run the tests:

```bash
uv run pytest
```

Run Ruff:

```bash
uv run ruff check .
```

Automatically fix supported Ruff issues:

```bash
uv run ruff check . --fix
```

Run mypy:

```bash
uv run mypy src
```

Run all checks:

```bash
uv run ruff check .
uv run pytest
uv run mypy src
```

## Planned Work

This project is a work in progress. Potential future components include:

* Remote Ollama inference through a MacBook M1 Pro
* Secure SSH tunneling to a remote Ollama server
* Read-only filesystem isolation for the AI process
* Semantic search using embeddings
* Retrieval-augmented generation
* Date and topic filters
* Weekly journal reviews
* Source-linked pattern analysis
* Human-approved long-term insights
* Encrypted cloud backups
* Backup restoration testing
* Automatic indexing of new or changed entries
* A local graphical interface
* Voice journal transcription
* Performance benchmarking across models and hardware

## Design Principles

### The LLM is not the journal database

The journal remains the durable source of truth.

Python controls:

* Filesystem access
* Document selection
* Retrieval
* Prompt construction
* Model communication
* Output storage

Ollama is used only as a language and reasoning component over selected source material.

### Source entries remain unchanged

Human-written journal entries are source material. AI output is stored separately and should never silently replace or rewrite the original writing.

### Interpretations require evidence

Future analysis should clearly distinguish:

* Direct statements
* Observed patterns
* Possible interpretations
* Suggested questions or experiments

Generated claims should link back to the journal entries that support them.

## Privacy Notice

Journal entries may contain highly sensitive information.

Do not:

* Commit journal files to Git
* Upload the decrypted journal directory
* Expose Ollama directly to the public internet
* Send entries to an untrusted remote model server
* Store generated analyses outside the encrypted volume
* Treat AI interpretations as verified facts

This project is intended for private, local use.

## License

No license has been selected yet.
