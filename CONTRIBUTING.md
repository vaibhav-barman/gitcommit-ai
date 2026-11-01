# Contributing to GitCommit AI

Thanks for helping out. This guide covers setup, the checks every change must pass, and the
design rules that keep the project reliable.

## Setup

You need Python 3.11 or newer, [uv](https://github.com/astral-sh/uv), and Git.

```bash
git clone https://github.com/vaibhav-barman/gitcommit-ai.git
cd gitcommit-ai
uv sync --all-extras
```

`--all-extras` installs every provider SDK. The backend tests import all of them (they are mocked,
so no API keys or network access are needed).

## Checks

Run these before opening a pull request. CI runs the same commands.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run pytest
```

- **Ruff** lints and formats (line length 99).
- **mypy** runs in strict mode. Annotate everything, including tests.
- **pytest** must pass with no network access.

CI tests Python 3.11, 3.12, and 3.13. To reproduce one locally without disturbing your main
environment:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/venv311 uv sync --all-extras --python 3.11
UV_PROJECT_ENVIRONMENT=/tmp/venv311 uv run --python 3.11 pytest
```

Do not use syntax newer than 3.11 (for example PEP 695 `def f[T]()` generics or `type` statements).

## Design rules

1. **Dependencies point one way**: `cli → agents → context → git/core`. `llm` is used by `agents`
   and by the CLI to build a provider. Nothing in `git`, `context` or `core` may import from a
   layer above it.
2. **The deterministic layers stay LLM-free.** `git` and `context` must produce the same output for
   the same repository. LLM calls belong in `agents`.
3. **Git access is explicit and safe.** Run Git with an argument list (never `shell=True`), with a
   timeout, through `RepositoryInspector`. The only code that writes to a repository is
   `GitCommitter` (commits) and `HookInstaller` (the hook file). Neither stages or unstages
   anything, and `GitCommitter` checks the staleness guard first.
4. **Prefer rules to prompts.** If something can be checked deterministically (format, length,
   wrapping), do that in code and keep the LLM for what rules cannot judge.
5. **Failures are typed.** Raise a `GitCommitAIError` subclass with an actionable message. The CLI
   turns these into a clean `Error: ...` line and exit code 1. The commit hook must never fail a
   commit.
6. **No dead code, no TODO comments, no commented-out blocks.**

## Tests

- **Never touch the network.** Use `FakeLLM` or the `scripted_llm` helper
  (`tests/fixtures/scripted_llm.py`), which can also inject provider errors. Provider backends are
  tested with mocked SDK clients.
- **Use real temporary Git repositories** for anything that touches Git
  (`tests/fixtures/git_repo.py`). Never run Git against the working repository.
- Put the tests in the same commit as the code they cover. New behavior needs tests for the
  failure paths as well as the happy path.
- Settings tests must not depend on a `.env` in your checkout: set `GITCOMMIT_AI_ENV_FILE` to an
  empty string (the CLI test modules do this in an autouse fixture).

## Adding a provider

Add `src/git_commit_ai/llm/<name>_provider.py` implementing `LLMProvider`, mapping SDK failures to
`RateLimitError`, `TransientLLMError` (timeouts, connection drops, 5xx) or `LLMError`; register it
in `llm/factory.py` and `ProviderName`; add an optional extra in `pyproject.toml`; and add mocked
tests next to the existing backend tests.

## Commits and pull requests

This repository uses [Conventional Commits](https://www.conventionalcommits.org/) with a scope,
for example `feat(agents): add a critic rule`.

- Imperative subject under 72 characters, lowercase after the prefix, no trailing period.
- Add a body whenever the *why* is not obvious.
- Keep commits atomic: each one should pass the checks on its own.
- Update `CHANGELOG.md` under **Unreleased** for user-visible changes, and the README or
  `docs/architecture.md` when behavior or design changes.

## Reporting problems

Open an issue with the command you ran, the output (with API keys removed), and the output of
`git-ai-commit config`, which masks secrets.
