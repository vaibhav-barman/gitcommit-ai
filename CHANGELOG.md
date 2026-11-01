# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

No version has been released yet; everything below is under **Unreleased**.

## [Unreleased]

### Changed

- **No provider SDKs.** Every provider is called over plain HTTP with `httpx`, so `pipx install .`
  works for every provider with nothing else to install. The `openai`, `anthropic`, `gemini`,
  `ollama` and `all` extras still exist but install nothing; they will be removed in the release
  after next. Provider errors are mapped in one place: a rejected key, a retired model and a
  provider's own error message (with keys redacted) are now reported distinctly.
- Settings can be saved in `config.toml` and keys in `credentials.toml` (mode 0600) in the per-user
  config directory (`GITCOMMIT_AI_CONFIG_DIR`). Both rank below the environment and `.env`, so
  setups configured through variables behave exactly as before.

### Added (this release)

- **OpenRouter and Hugging Face providers** (`openrouter`, `OPENROUTER_API_KEY`; `huggingface`,
  `HF_TOKEN`), sharing one OpenAI-compatible backend with OpenAI.
- **Ollama keys**: `OLLAMA_API_KEY` is sent as a Bearer token, for Ollama Cloud
  (`GITCOMMIT_AI_OLLAMA_URL=https://ollama.com`) or a protected server. A warning appears when a
  key would go over plain `http://` to another machine.
- `GEMINI_API_KEY` is accepted as well as `GOOGLE_API_KEY`.

### Removed

- `ProviderNotInstalledError`: there are no optional SDKs left to be missing.

### Added

- **Git intelligence layer**: repository inspection, staged/unstaged/amend diff extraction with
  rename and binary detection, history extraction, and staged-tree snapshots for staleness
  detection.
- **Repository context engine**: commit normalization (Conventional Commits, bracket prefixes,
  Gitmoji), statistical convention detection, style profiling, and recency-aware selection of
  relevant historical examples. CLI commands `inspect`, `diff`, `history`, `context`, `examples`.
- **Agent graph** (LangGraph): diff analyst, context retrieval, generator and critic nodes joined by
  a capped reflection loop that falls back to the best draft. The critic applies deterministic
  convention and style rules and uses the LLM only to check the message against the diff.
- **LLM providers**: OpenAI, Anthropic, Gemini and Ollama behind one protocol, as optional extras
  (`openai`, `anthropic`, `gemini`, `ollama`, `all`), plus a deterministic `FakeLLM` for tests.
- **`generate` command** with `--provider`, `--model`, `--dry-run`, `--yes`, `--accept-partial`,
  `--max-iterations`, `--hint`, `--amend`, `--no-verify` and `--verbose`; the message goes to stdout
  and diagnostics to stderr. `--yes` refuses to commit a draft left unfinished by a provider error
  unless `--accept-partial` is given. Handles nothing staged, empty repositories, binary-only diffs
  and very large diffs (budgeted per file, with a notice).
- **Evaluation harness** (`scripts/eval/`, not installed): replays a repository's last N
  non-merge commits through the agent graph, using each commit's diff against its parent and only
  older commits as history, and scores the result with deterministic checks (convention, subject
  length, imperative mood, capitalization, punctuation, scope and body agreement, critic findings
  before and after reflection, rounds used). Writes JSON and Markdown reports; `--offline` uses a
  deterministic stand-in for the model so it needs no network.
- **Documentation check** (`python -m scripts.check_docs`, also run by the test suite): every command,
  flag, environment variable, provider default, path, link, heading anchor and Mermaid diagram in the
  docs is checked against the code, and the layer diagrams against the real import graph. A test
  enforces the dependency direction between packages.
- **Demo tooling**: `docs/demo.tape` (a VHS script), `scripts/demo/prepare.sh` (builds the throwaway
  repository it records) and `make demo`. The GIF itself is recorded with a live provider and is
  not committed.
- **`git ai-commit`** via a `git-ai-commit` console script, which also offers `config` and `hook`.
- **Resilience**: rate limits and transient provider errors (timeouts, dropped connections, 5xx)
  are retried with bounded exponential backoff (`GITCOMMIT_AI_MAX_RETRIES`,
  `GITCOMMIT_AI_RETRY_BASE_DELAY`, `GITCOMMIT_AI_RETRY_MAX_DELAY`). A provider error after the first
  draft returns the best draft so far with a visible warning instead of aborting.
- **Settings**: `.env` loading with precedence flags > environment > `.env` > defaults. A `.env`
  is read only from the current directory or the Git repository root, never from parent
  directories or the home directory. A `GITCOMMIT_AI_ENV_FILE` override, and a `config` command
  that shows each value's source and masks secrets.
- **Message bodies**: a short body wrapped at 72 columns is generated when the repository uses
  bodies or the diff is large, with new critic rules for the body.
- **Staleness guard** on committing: the staged tree and `HEAD` must be unchanged since the message
  was written.
- **`prepare-commit-msg` hook**: `hook install` / `hook uninstall`. An existing hook is backed up,
  chained and restored on uninstall; the hook never blocks a commit.
- **Repository states**: merges, cherry-picks and reverts in progress are refused (Git has prepared
  the message), a rebase proceeds with a note, and a detached `HEAD` is warned about.
- **CI** (GitHub Actions): Ruff, mypy and pytest on Python 3.11, 3.12 and 3.13, plus a job that
  builds the package and runs both entry points from a clean install.
- `LICENSE` (MIT), `CONTRIBUTING.md`, this changelog, a `py.typed` marker, and package metadata
  (URLs, classifiers).

### Changed

- The README is now a landing page: logo, a captured sample run, a comparison with one-shot
  prompts, a quick start, Mermaid diagrams, collapsible reference sections and an evaluation
  section whose results are still to be filled in. `docs/architecture.md` is aligned with it.
- Minimum supported Python is 3.11.
- The missing-SDK error also gives the `pipx` command, since `pip install` would target the wrong
  environment for a pipx install.
- A blank line between the subject and body is now a critic error rather than a warning, because
  Git folds an unseparated body into the subject.

### Fixed

- The empty-tree hash was hard-coded with a wrong digit in the snapshot fallback; it is now asked of
  Git, which also supports SHA-256 repositories.
- `docs/architecture.md` listed schema fields that did not match the code (notably
  `NormalizedCommit`). The schema section is now generated from the models.

[Unreleased]: https://github.com/vaibhav-barman/gitcommit-ai/commits/main
