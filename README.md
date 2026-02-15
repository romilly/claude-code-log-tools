# claude-code-log-tools

Tools for capturing, searching and learning from Claude Code logs.

## Features

- **Import Claude Code JSONL logs** into PostgreSQL for analysis
- **Idempotent imports** — safe to re-run; only new entries are added
- **Full-text search** across all conversation content via PostgreSQL tsvector
- **Session summaries** captured from log metadata
- **Query tools** for finding failed tool calls, tool usage patterns, and more

## Quick Start

1. Set up PostgreSQL 13+ and create a `claude_logs` database
2. Copy `.env.example` to `.env` and configure your database connection
3. Install dependencies: `pip install -r requirements.txt`
4. Open `notebooks/01_import_logs.ipynb` and run all cells

The import notebook discovers JSONL files in `~/.claude/projects/`, creates the schema, and imports all log entries. Re-running imports only new data.

## Schema

Four tables: **sessions**, **messages**, **content_blocks**, and **import_metadata**.

See [database design](docs/DESIGN.md) for full details.

## Progress

- [2025-12-22](plan/progress-2025-12-22.md) — Initial setup: schema, import notebook, PostgreSQL deployment
- [2026-02-15](plan/progress-2026-02-15.md) — Idempotent imports, session summaries, entry filtering

## Installation

```bash
pip install -e .
```

## Development

```bash
pip install -e .[test]
pytest
```