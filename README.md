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
* Tracking journal files in a local SQLite index
* Dividing source Markdown into deterministic, searchable chunks during indexing
* Storing chunk text and line metadata in the local SQLite index
* Analyzing one explicitly selected journal entry
* Connecting to a local or remote Ollama server
* Saving generated analyses under `generated/analyses`
* Preserving original journal files unchanged
* Reading optional settings from `~/.config/journal-ai/config.toml`
* Overriding any configured setting from the command line
* Automated tests, linting, and type checking

Planned features are documented later in this README.

## Project Structure

```text
journal-ai/
├── src/
│   └── journal_ai/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── chunking.py
│       ├── hashing.py
│       ├── index_database.py
│       ├── index_service.py
│       ├── journal_reader.py
│       ├── models.py
│       ├── ollama_client.py
│       ├── output_writer.py
│       └── prompts.py
├── tests/
│   ├── conftest.py
│   ├── test_analyze_save_workflow.py
│   ├── test_chunking.py
│   ├── test_cli_config.py
│   ├── test_cli_index.py
│   ├── test_config.py
│   ├── test_hashing.py
│   ├── test_index_chunks.py
│   ├── test_index_database.py
│   ├── test_index_service.py
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

To avoid repeating these options, put them in the configuration file described below.

## Journal Index

### What the index is

The index is a small local SQLite inventory of the Markdown files in the mounted journal. It records one row per source file and answers four questions:

* Which files are new?
* Which files changed?
* Which files are unchanged?
* Which previously indexed files were deleted?

It also records when the last successful index run finished.

### Why it exists

Later features such as embeddings, semantic search, and weekly reviews should not reprocess the whole journal every time. The index gives them a cheap, reliable way to work on only the entries that actually changed.

The index is **not** the journal. The Markdown files remain the source of truth.

### Database location

The database path is derived from the configured journal path:

```text
<journal_path>/.journal-ai/index.sqlite
```

With the default journal path this is:

```text
~/journal/.journal-ai/index.sqlite
```

Keeping it there means the index lives inside the encrypted volume, next to the journal it describes, and is unlocked and locked together with it. The `.journal-ai` directory is excluded from source discovery, so the application never indexes or analyzes its own state.

There is deliberately no configuration setting for this path. An index describes exactly one journal, so it is derived from `journal_path` instead of being configured separately.

### Data stored

For each Markdown file the index stores:

| Column | Meaning |
| --- | --- |
| `relative_path` | Path relative to the mounted journal directory |
| `content_hash` | SHA-256 hex digest of the file's bytes |
| `file_size` | Size in bytes |
| `modified_at` | Filesystem modification time, UTC |
| `first_indexed_at` | When the file first entered the index, UTC |
| `last_indexed_at` | When the stored record was last written, UTC |

A second table, `index_metadata`, holds the schema version, the time of the last successful index run, and a `chunking_signature` that records the chunking settings used to build the stored chunks.

A third table, `chunks`, stores deterministic segments of each source Markdown file. Each chunk row links to a document through `document_id`, includes inclusive one-based `start_line` and `end_line` values, a SHA-256 `content_hash`, and the exact chunk text as it appears in the source file.

**Chunk text is derived data.** It is rebuilt from the Markdown files during indexing and is not a substitute for the journal itself. Because the database lives under `.journal-ai` inside the mounted journal, chunk text remains encrypted at rest together with the rest of the volume.

**No Ollama calls occur.** Indexing and chunking are pure filesystem and SQLite work. They make no network requests of any kind.

**Schema upgrades are not migrated.** During this pre-release phase, an index with a different schema version is rejected. Rebuild it from Markdown with `uv run journal-ai index --rebuild`. The database is disposable; journal files are never modified.

### Chunking rules (high level)

During `journal-ai index`, new and content-changed Markdown files are read once, divided into deterministic chunks, and written to SQLite. Unchanged files keep their existing chunk rows unless the chunking configuration has changed.

The index stores a `chunking_signature` derived from `target_characters`, `max_characters`, and `overlap_characters`. The same source content and the same chunking settings leave existing chunks untouched. Changing those settings re-chunks currently indexed source documents without classifying them as content-changed. `minimum_characters` is not a supported setting.

Structural boundaries are preferred in this order:

1. Markdown headings stay with the content below them when possible.
2. Blank-line-separated paragraphs.
3. Logseq top-level bullets, keeping nested child bullets with their parent when they fit.
4. Sentences, lines, or character positions only when a block exceeds the configured maximum size.

Overlap is used only when oversized content must be split; it is not added between normal sections.

Default size settings (overridable in configuration):

| Setting | Default |
| --- | --- |
| `chunking.target_characters` | 1000 |
| `chunking.max_characters` | 1600 |
| `chunking.overlap_characters` | 150 |

Example configuration:

```toml
[chunking]
target_characters = 1000
max_characters = 1600
overlap_characters = 150
```

### Update the index

```bash
uv run journal-ai index
```

Example output:

```text
Index updated.
Documents
New:       2
Changed:   1
Unchanged: 14
Deleted:   0
Chunks
Created:   8
Removed:   3
Total:     47
```

Running it again without changing any entries reports:

```text
Index already current.
Documents
New:       0
Changed:   0
Unchanged: 17
Deleted:   0
Chunks
Created:   0
Removed:   0
Total:     47
```

### Show index status

```bash
uv run journal-ai index --status
```

Example output:

```text
Database: /home/example/journal/.journal-ai/index.sqlite
Documents: 17
Chunks: 47
Last updated: 2026-07-31T13:24:18+00:00
```

Status reads the database only. It does not scan the journal, and it does not create the database.

### Rebuild the index

```bash
uv run journal-ai index --rebuild
```

This deletes the database file and rebuilds it from the current source files, so every discovered entry is reported as new and all chunks and chunking metadata are recreated. Use it when the schema version does not match, after changing how the index is structured, or if the database is ever damaged.

Only the index database and its SQLite sidecar files are deleted. Journal entries are never touched.

### The index is rebuildable

The database is disposable application state. Deleting it loses nothing that matters:

```bash
rm ~/journal/.journal-ai/index.sqlite
uv run journal-ai index
```

Because the index can always be rebuilt from the Markdown files, it does not need to be backed up separately, and a damaged database is never a data loss event.

### How change detection works

Each indexed run compares the files it discovers with the records already stored:

| Situation | Classification |
| --- | --- |
| No database record | new |
| Record exists, content hash differs | changed |
| Record exists, content hash matches | unchanged |
| Record exists, file no longer on disk | deleted |

The content hash is authoritative. File size and modification time are stored as metadata and are refreshed when they drift, but they never decide that content changed. Touching a file, or restoring it from a backup, therefore leaves it classified as unchanged and does not mark it for reprocessing.

Changing chunking configuration is tracked separately through `chunking_signature`. It can replace stored chunks without classifying the source files as content-changed.

All inserts, updates, deletions, chunk replacements, and signature writes for one run are applied inside a single SQLite transaction. If any statement fails, the whole run is rolled back and the index is left exactly as it was.

The index command fails with a clear error when the journal is locked or not mounted.

## Configuration

Settings come from an optional TOML file. The default location is:

```text
~/.config/journal-ai/config.toml
```

The file is optional. When it does not exist, the built-in defaults are used and no error is reported.

### Precedence

```text
Explicit command-line option
        ↓
Configuration file value
        ↓
Built-in default
```

### Example config.toml

```toml
journal_path = "/home/example-user/journal"

[ollama]
url = "http://localhost:11434"
model = "qwen3.5:4b"
timeout_seconds = 900
num_predict = 300
think = false
```

Replace `/home/example-user/journal` with your own mount point. A leading `~` is expanded, so `journal_path = "~/journal"` also works.

### Supported settings

| Setting | Type | Default | Meaning |
| --- | --- | --- | --- |
| `journal_path` | string | `~/journal` | Mounted journal directory |
| `ollama.url` | string | `http://localhost:11434` | Ollama server URL |
| `ollama.model` | string | `qwen3.5:4b` | Model used for analysis |
| `ollama.timeout_seconds` | number > 0 | `900` | HTTP request timeout |
| `ollama.num_predict` | integer > 0 | `300` | Maximum generated tokens |
| `ollama.think` | boolean | `false` | Whether the model may think before answering |
| `chunking.target_characters` | integer > 0 | `1000` | Preferred chunk size while merging blocks |
| `chunking.max_characters` | integer > 0 | `1600` | Maximum characters in a normal chunk |
| `chunking.overlap_characters` | integer ≥ 0 | `150` | Overlap used only when oversized content is split |

Limiting output tokens reduces response time on slower hardware and keeps single-entry analyses concise. Thinking is disabled by default so the model does not spend the output-token allowance on hidden reasoning without producing a visible answer.

### Validation

Configuration mistakes fail loudly rather than silently:

* Invalid TOML is rejected.
* Wrong value types are rejected.
* Zero or negative `timeout_seconds` and `num_predict` values are rejected.
* Empty strings are rejected.
* **Unknown keys are rejected**, so a typo such as `jurnal_path` reports an error instead of being ignored.

### Use a different configuration file

```bash
uv run journal-ai --config /tmp/config.toml list
uv run journal-ai --config /tmp/config.toml analyze journals/2026_07_28.md
```

A missing file at an explicitly supplied `--config` path is also treated as "use the defaults".

### Command-line overrides

Every setting has a command-line override. None of them are required.

```bash
uv run journal-ai --journal-path /mnt/journal list

uv run journal-ai analyze journals/2026_07_28.md \
  --model qwen3.5:4b \
  --ollama-url http://192.168.1.42:11434 \
  --timeout-seconds 120 \
  --num-predict 500 \
  --no-think
```

`--think` and `--no-think` are mutually exclusive. These options may be given before or after the subcommand:

```bash
uv run journal-ai --model qwen3.5:4b analyze journals/2026_07_28.md
```

### Do not store secrets in this file

The configuration file is plain text outside the encrypted volume.

Never put the following in `config.toml`:

* Passwords
* gocryptfs encryption keys or passphrases
* API tokens
* Journal content or quotations from entries
* Anything else that must stay encrypted

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
11. The documents table stores file metadata and hashes; chunk text is derived data rebuildable from source Markdown.
12. The index database stays inside the encrypted mount under `.journal-ai`.

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
* Commit the index database
* Upload the decrypted journal directory
* Expose Ollama directly to the public internet
* Send entries to an untrusted remote model server
* Store generated analyses outside the encrypted volume
* Treat AI interpretations as verified facts

This project is intended for private, local use.

## License

No license has been selected yet.
