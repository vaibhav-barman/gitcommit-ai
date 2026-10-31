# GitCommit AI

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/package%20manager-uv-blueviolet.svg)](https://github.com/astral-sh/uv)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)

**GitCommit AI** is a production-oriented developer tool designed to generate clean, semantic, and deeply contextual Git commit messages. It combines deterministic Git repository intelligence with future agentic AI workflows.

---

## 🌟 Long-Term Architectural Vision

Modern software changes are complex, cross-cutting, and full of context that simple one-shot LLM prompts fail to capture. GitCommit AI is built to overcome these limitations through an agentic pipeline:

- **LangGraph Multi-Agent Orchestration**: A structured state graph coordinating specialized AI agents.
- **Specialized Agents**:
  - *Diff Analyst Agent*: Deconstructs unified diffs, identifies architectural changes, and filters noise.
  - *Context Retrieval Agent*: Analyzes repository history, commit conventions, and branch semantics.
  - *Commit Generator Agent*: Crafts high-quality Conventional Commit messages (type, scope, subject, body, breaking changes).
  - *Reflection & Critique Agent*: Evaluates proposed commits against team standards, repository history, and diff reality.
- **Staleness Guarding**: Tree-hash snapshotting ensures that any changes staged while generation is underway abort or trigger re-analysis before any commit can occur.
- **Hybrid Cloud / Local LLM Inference**: Pluggable backend support for OpenAI, Anthropic, Google Gemini, Ollama, and local llama.cpp servers.

> **Current Milestone**: Phase 1 implements the deterministic, LLM-free foundation: safe repository inspection, staged/unstaged diff extraction, historical commit analysis, snapshot management, and a standalone CLI.

---

## 🚀 Phase 1 Capabilities

- **Repository Inspection (`RepositoryInspector`)**:
  - Detects Git repository boundary and resolves absolute root path.
  - Tracks branch name and detects detached HEAD state.
  - Identifies staged files, unstaged modified files, and untracked files.
  - Safely handles repositories with zero commits (empty repos).
- **Diff Extraction (`DiffExtractor`)**:
  - Focuses by default strictly on **staged** changes.
  - Generates unified diff output, numstat counts (additions and deletions), and name-status change codes.
  - Detects file renames (`old_path -> path`) and flags binary files (`is_binary`).
  - Never automatically stages, unstages, modifies, or discards repository content.
- **Historical Commit Extraction (`CommitHistoryExtractor`)**:
  - Retrieves recent non-merge commits (configurable count, default 10).
  - Uses explicit field and commit delimiters in `git log` formatting to robustly parse multiline bodies and special characters.
- **Snapshot Management (`SnapshotManager`)**:
  - Captures deterministic staged tree hashes (`git write-tree`) for race-condition and staleness detection.
- **Command-Line Interface**:
  - `gitcommit-ai inspect`
  - `gitcommit-ai diff`
  - `gitcommit-ai history`

---

## 🛠️ Installation & Setup

GitCommit AI uses [uv](https://github.com/astral-sh/uv) for fast, deterministic Python environment and dependency management.

### Prerequisites

- Python 3.12 or newer
- [uv](https://github.com/astral-sh/uv) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`)
- Git installed and accessible on your `PATH`

### Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/vaibhav-barman/gitcommit-ai.git
cd gitcommit-ai

# Sync dependencies and create virtual environment
uv sync
```

---

## 💻 CLI Usage

Invoke commands using `uv run gitcommit-ai` or directly within an activated environment:

### 1. Inspect Repository State
Inspect the current repository's root, branch, detached HEAD status, and staged/unstaged/untracked files:

```bash
uv run gitcommit-ai inspect
# Or inspect a specific target path
uv run gitcommit-ai inspect --path /path/to/repo
```

### 2. View Diff Statistics
View aggregate statistics and changed file breakdowns for staged changes:

```bash
uv run gitcommit-ai diff
# Inspect unstaged changes instead
uv run gitcommit-ai diff --unstaged
```

### 3. View Recent Commit History
Display the recent non-merge commits:

```bash
uv run gitcommit-ai history
# Customize commit count
uv run gitcommit-ai history --count 5
```

---

## 🧪 Testing & Quality Assurance

GitCommit AI maintains strict quality standards: 100% type-annotated (`mypy` strict mode), linted and formatted with `ruff`, and verified against temporary Git repositories using `pytest`.

```bash
# Run complete test suite (unit + integration)
uv run pytest -v

# Run linter
uv run ruff check .

# Check formatting
uv run ruff format --check .

# Run static type checking
uv run mypy src/ tests/
```

Tests run in isolated temporary repositories generated by pytest fixtures and will never modify your working repository.

---

## 📂 Project Structure

```
gitcommit-ai/
├── src/
│   └── git_commit_ai/
│       ├── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py       # Pydantic configuration model
│       │   ├── exceptions.py   # Application exception hierarchy
│       │   └── schemas.py      # Typed Pydantic models & StrEnums
│       ├── git/
│       │   ├── __init__.py
│       │   ├── diff.py         # Staged & unstaged diff extraction
│       │   ├── history.py      # Delimited historical commit parsing
│       │   ├── repository.py   # Subprocess-safe repository inspector
│       │   └── snapshots.py    # Staged tree identity & staleness guard
│       └── cli/
│           ├── __init__.py
│           └── main.py         # Typer CLI application
├── tests/
│   ├── conftest.py             # Shared pytest fixtures
│   ├── fixtures/
│   │   └── git_repo.py         # Isolated temp git repository factories
│   ├── unit/
│   │   ├── test_config.py      # Configuration tests
│   │   └── test_schemas.py     # Schema & enum unit tests
│   └── integration/
│       ├── test_cli.py         # CLI integration tests
│       ├── test_diff.py        # Diff extraction & rename tests
│       ├── test_history.py     # Commit history extraction tests
│       ├── test_repository.py  # Repository inspection tests
│       └── test_snapshots.py   # Snapshot comparison tests
├── docs/
│   └── architecture.md         # Detailed architectural documentation
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 🗺️ Roadmap

- **Phase 1 (Completed)**: Project Foundation & Git Intelligence Layer.
- **Phase 2**: LLM Integration & Prompt Engineering (Structured Outputs, Provider abstraction).
- **Phase 3**: Agentic Orchestration with LangGraph (Diff Analyst, Context Retriever, Generator, Critic).
- **Phase 4**: Interactive CLI & Native Git Subcommand Integration (`git-commit-ai`).
