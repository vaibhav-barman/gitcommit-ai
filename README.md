# GitCommit AI

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/package%20manager-uv-blueviolet.svg)](https://github.com/astral-sh/uv)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)

**GitCommit AI** is a production-oriented developer tool designed to generate clean, semantic, and deeply contextual Git commit messages. It combines deterministic Git repository intelligence and historical style analysis with future agentic AI workflows.

---

## 🌟 Long-Term Architectural Vision

Modern software changes are complex, cross-cutting, and full of context that simple one-shot LLM prompts fail to capture. GitCommit AI is built to overcome these limitations through an agentic pipeline:

- **Deterministic Context Engineering**: Statistical convention detection, repository style profiling, and recency-decayed historical example retrieval before any AI model is invoked.
- **LangGraph Multi-Agent Orchestration**: A structured state graph coordinating specialized AI agents.
- **Specialized Agents**:
  - *Diff Analyst Agent*: Deconstructs unified diffs, identifies architectural changes, and filters noise.
  - *Context Retrieval Agent*: Synthesizes repository history, commit conventions, and branch semantics.
  - *Commit Generator Agent*: Crafts high-quality Conventional Commit messages (type, scope, subject, body, breaking changes).
  - *Reflection & Critique Agent*: Evaluates proposed commits against team standards, repository history, and diff reality.
- **Staleness Guarding**: Tree-hash snapshotting ensures that any changes staged while generation is underway abort or trigger re-analysis before any commit can occur.
- **Hybrid Cloud / Local LLM Inference**: Pluggable backend support for OpenAI, Anthropic, Google Gemini, Ollama, and local llama.cpp servers.

> **Current Milestone**: Phase 1 (Git Intelligence Layer) and Phase 2 (Repository Context Engine) are complete. All repository inspection, diff extraction, commit normalization, statistical convention detection, style profiling, and heuristic example retrieval run 100% deterministically with zero LLM dependencies.

---

## 🚀 Implemented Capabilities

### 1. Git Intelligence Layer (Phase 1)
- **Repository Inspection (`RepositoryInspector`)**:
  - Detects Git repository boundaries and resolves absolute root paths.
  - Tracks branch names and detects detached HEAD states.
  - Identifies staged files, unstaged modified files, and untracked files.
  - Safely handles newly initialized repositories with zero commits (empty repos).
- **Diff Extraction (`DiffExtractor`)**:
  - Focuses by default strictly on **staged** changes (`DiffMode.STAGED`).
  - Generates unified diff output, numstat counts (additions and deletions), and name-status change codes.
  - Detects file renames (`old_path -> path`) and flags binary files (`is_binary`).
  - Never automatically stages, unstages, modifies, or discards repository content.
- **Historical Commit Extraction (`CommitHistoryExtractor`)**:
  - Retrieves recent non-merge commits (configurable count, default 10).
  - Uses explicit field and commit delimiters in `git log` formatting to robustly parse multiline bodies and special characters.
- **Snapshot Management (`SnapshotManager`)**:
  - Captures deterministic staged tree hashes (`git write-tree`) for race-condition and staleness detection.

### 2. Repository Context Engine (Phase 2)
- **Commit Normalization (`CommitNormalizer`)**:
  - Parses and decomposes raw commit messages into semantic components (type, scope, breaking flag, subject, body, footers).
  - Normalizes Conventional Commits, bracket-prefixed commits (`[AUTH]`), Gitmoji (both shortcodes and unicode), and unstructured plain messages.
  - Extracts breaking change footers (`BREAKING CHANGE:` / `BREAKING-CHANGE:`) and subject exclamation marks (`feat!: ...`).
- **Commit Convention Detection (`CommitConventionDetector`)**:
  - Statistically identifies repository convention (`conventional`, `scoped_conventional`, `gitmoji`, `bracket_prefixed`, `plain`).
  - Calculates confidence scores adjusted for sample size penalties when evaluating small histories.
  - Generates human-readable evidence summaries explaining why a convention was detected.
- **Repository Style Profiling (`RepositoryStyleAnalyzer`)**:
  - Analyzes subject length metrics (average and median character counts).
  - Evaluates capitalization patterns (`lowercase`, `uppercase`, `mixed`).
  - Detects trailing punctuation standards (e.g. trailing periods vs. no periods).
  - Quantifies body usage frequency and scope usage frequency.
  - Identifies common repository scopes and frequency distributions across commit types.
  - Measures imperative mood adherence using heuristic verb analysis.
  - Detects temporal convention shifts between historical baselines and recent commits.
- **Recency-Aware Example Retrieval (`HistoricalExampleSelector`)**:
  - Employs a multi-factor heuristic scoring function matching staged changes against commit history:
    - Scope matching (+3.0)
    - Path and filename matching (+2.0)
    - Inferred change type matching (+1.5)
    - Keyword overlap (+0.5 to +1.5)
    - Recency decay weighting ($1 / (1 + \lambda \cdot \text{rank})$)
  - Provides explainable selection reasons for every retrieved example.
- **Unified Context Engine (`RepositoryContextEngine`)**:
  - Coordinates normalizer, convention detector, style analyzer, and example selector into a cohesive `RepositoryContext` payload ready for future LLM prompt injection.

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
Inspect the current repository root, branch, detached HEAD status, and categorized files:

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
Display recent non-merge commits:

```bash
uv run gitcommit-ai history
# Customize commit count
uv run gitcommit-ai history --count 5
```

### 4. Analyze Commit Conventions & Writing Style
Analyze repository commit patterns, subject length, capitalization, and scope usage:

```bash
uv run gitcommit-ai context
# Analyze with custom commit sample size
uv run gitcommit-ai context --count 50
```

*Example Output:*
```text
Repository Commit Context
  Convention:     SCOPED_CONVENTIONAL (confidence: 100.0%)
  Analyzed:       10 commits

Style Characteristics
  Subject length: 45.2 chars avg / 44.5 chars median
  Capitalization: lowercase
  Punctuation:    no_trailing_period
  Body frequency: 100.0%
  Scope usage:    80.0%
  Imperative:     70.0%

Common Types:
  feat: 8
  docs: 1
  test: 1

Common Scopes:
  context, cli, git

Evidence:
  - 10 of 10 commits (100.0%) follow Conventional Commits format.
  - 8 of 10 conventional commits (80.0%) specify a scope.
```

### 5. Retrieve Relevant Historical Examples
Retrieve historical commits matching your current staged or unstaged changes:

```bash
# Match against staged changes (default)
uv run gitcommit-ai examples

# Match against unstaged changes
uv run gitcommit-ai examples --unstaged

# Limit maximum number of examples
uv run gitcommit-ai examples --max-examples 3
```

*Example Output:*
```text
Relevant Historical Examples for Unstaged Changes (5)

  1. [8e33257] feat(cli): add Typer CLI with inspect, diff, and history commands (Score: 3.57)
     - Implement gitcommit-ai inspect to view repo root, branch, detached HEAD, staged/unstaged/untracked files...
     ✓ Matches scope 'cli' found in changed files
     ✓ Matches inferred change type 'feat'
     ✓ Shares keywords with change: cli

  2. [16a04e2] feat(context): detect repository commit conventions (Score: 1.74)
     - Implement CommitConventionDetector using statistical distribution analysis...
     ✓ Matches inferred change type 'feat'
     ✓ Shares keywords with change: commit
```

---

## 🧪 Testing & Quality Assurance

GitCommit AI maintains strict quality standards: 100% type-annotated (`mypy` strict mode), linted and formatted with `ruff`, and verified against temporary Git repositories using `pytest`.

```bash
# Run complete test suite (88 unit + integration tests)
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
│       │   ├── config.py             # Pydantic configuration model
│       │   ├── exceptions.py         # Application exception hierarchy
│       │   └── schemas.py            # Typed Pydantic models & StrEnums
│       ├── git/
│       │   ├── __init__.py
│       │   ├── diff.py               # Staged & unstaged diff extraction
│       │   ├── history.py            # Delimited historical commit parsing
│       │   ├── repository.py         # Subprocess-safe repository inspector
│       │   └── snapshots.py          # Staged tree identity & staleness guard
│       ├── context/
│       │   ├── __init__.py
│       │   ├── normalizer.py         # Semantic commit normalization & parsing
│       │   ├── convention_detector.py# Statistical convention detection & confidence
│       │   ├── style_analyzer.py     # Repository style profiling & metrics
│       │   ├── example_selector.py   # Multi-factor heuristic example retrieval
│       │   └── engine.py             # Context engine orchestration pipeline
│       └── cli/
│           ├── __init__.py
│           └── main.py               # Typer CLI application (5 commands)
├── tests/
│   ├── conftest.py                   # Shared pytest fixtures
│   ├── fixtures/
│   │   └── git_repo.py               # Isolated temp git repository factories
│   ├── unit/
│   │   ├── test_config.py            # Configuration tests
│   │   ├── test_schemas.py           # Schema & enum unit tests
│   │   ├── test_normalizer.py        # Commit normalizer tests
│   │   ├── test_convention_detector.py # Convention detector tests
│   │   ├── test_style_analyzer.py    # Style analyzer tests
│   │   ├── test_example_selector.py  # Example selector tests
│   │   └── test_context_engine.py    # Context engine tests
│   └── integration/
│       ├── test_cli.py               # CLI inspection & diff tests
│       ├── test_cli_context.py       # CLI context & examples tests
│       ├── test_diff.py              # Diff extraction & rename tests
│       ├── test_history.py           # Commit history extraction tests
│       ├── test_repository.py        # Repository inspection tests
│       └── test_snapshots.py         # Snapshot comparison tests
├── docs/
│   └── architecture.md               # Detailed architectural documentation
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 🗺️ Roadmap

- **Phase 1 (Completed)**: Project Foundation & Git Intelligence Layer.
- **Phase 2 (Completed)**: Repository Context Engine & Historical Commit Intelligence.
- **Phase 3**: LLM Integration & Prompt Engineering (Structured Outputs, Provider Abstraction for OpenAI, Anthropic, Gemini, Ollama).
- **Phase 4**: Agentic Orchestration with LangGraph (Diff Analyst, Context Retriever, Generator, Reflection Loop).
- **Phase 5**: Interactive Terminal UI & Native Git Subcommand Integration (`git-commit-ai`).
