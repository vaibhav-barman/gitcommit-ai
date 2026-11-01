# Phase 5 plan: guided onboarding

Status: plan, not implemented. Written 2026-11-02 against commit `91159be`.

Goal: `pipx install <package>`, run `git ai-commit`, answer a few prompts, done. Nobody should
have to pick a pip extra, export a variable, or think about where a `.env` lives. Everything that
works today with environment variables keeps working exactly as it does now.

Facts marked **(verified)** were checked on 2026-11-02 against the provider's documentation or
by calling the live endpoint with a fake key. Anything else is a design decision.

---

## 1. Final decisions

| # | Decision | Rationale | Rejected alternatives |
|---|---|---|---|
| D1 | **Drop every provider SDK. All six backends talk HTTP through `httpx`**, which becomes a declared dependency. | `httpx` is already installed transitively by `langgraph` → `langchain-core` (verified with `uv tree`), so the change adds no weight. It also removes `google-genai` (which pulls in `google-auth` and `cryptography`), `anthropic`, `openai` and `ollama`. `pipx install` then just works for every provider, and the wizard never has to install anything. | Keep SDK extras and have the wizard run `pipx inject` (fragile across pipx, uv, pip and venvs, and it changes the user's environment mid-run). Keep SDKs for the existing four and use HTTP only for the new two (two code paths for the same job). |
| D2 | **One `OpenAICompatibleBackend` serves OpenAI, OpenRouter and Hugging Face.** Each has a base URL and a few quirks. Anthropic, Gemini and Ollama get small native HTTP backends. | OpenRouter (`https://openrouter.ai/api/v1`) and Hugging Face Inference Providers (`https://router.huggingface.co/v1`) are both OpenAI chat-completions compatible (verified). Anthropic's own docs describe its OpenAI-compatible layer as a way "to test the Claude API", and Gemini's compatibility layer is `v1beta`. Native endpoints are the stable surface for those two, and they are small. | Route Anthropic and Gemini through their OpenAI-compatible endpoints (less faithful errors, no lifecycle data, Anthropic positions it for testing). Use LiteLLM (very heavy, and pins many packages). |
| D3 | **Menus use `questionary` (prompt_toolkit), behind a `Prompter` interface, with a plain numbered fallback.** | It is cross-platform: Windows Terminal, PowerShell, cmd, macOS and Linux. It supports arrow keys plus number shortcuts (`use_shortcuts`), type-to-filter for long lists (`use_search_filter`), hidden input (`password`), and an `unsafe_ask()` that raises `KeyboardInterrupt` (checked in the 2.1.1 source). Its cost is `prompt_toolkit` and `wcwidth`, both pure Python, roughly 1.5 MB. | In-house menus on `readchar` + `rich`: we would own scrolling, filtering and Windows key-code bugs. `InquirerPy`: last release 2022. `simple-term-menu`: no Windows support. `pick`: needs `windows-curses` on Windows. `beaupy`: small user base, newer. |
| D4 | **Config is a TOML file in a per-user config directory.** The directory is `$GITCOMMIT_AI_CONFIG_DIR`, else `%APPDATA%\gitcommit-ai` on Windows, else `$XDG_CONFIG_HOME/gitcommit-ai`, else `~/.config/gitcommit-ai`. That fallback applies on macOS too. | TOML is read by the stdlib (`tomllib`, 3.11+) and is human-editable. `~/.config` on macOS matches what `gh`, `git` and most developer CLIs use, and what users expect to find. The override makes tests hermetic. | `platformdirs` (an extra dependency, and on macOS it gives `~/Library/Application Support`, which CLI users rarely look in). JSON (no comments, unfriendly to edit). YAML (needs a dependency). |
| D5 | **Write TOML with a ~40-line in-house writer** limited to our schema: strings, ints, floats, bools, and one level of tables. Every write is checked by round-tripping through `tomllib`. | The schema is flat and fully known. A writer with a round-trip test is smaller than any dependency. | `tomli-w` (small, but one more dependency for about 40 lines). Hand-editing strings with regexes. |
| D6 | **Keys go in a separate `credentials.toml` with mode `0600`, in the same directory. The OS keyring is opt-in** through the `gitcommit-ai[keyring]` extra. An environment variable always wins over a stored key. | The file works everywhere: headless Linux, SSH, WSL, CI images, containers. This is the precedent set by `gh` (fallback), `hf` and AWS CLI. A keyring can pop a GUI permission dialog on macOS when the Python path changes (for example after `pipx upgrade`). Inside the commit hook, that would block `git commit`, which breaks requirement 5. Keeping keys apart from `config.toml` means the config can be shown, shared or committed to dotfiles safely. | Keyring by default (blocking dialogs, D-Bus failures on headless Linux). Keys inside `config.toml` (too easy to leak while sharing settings). Encrypting the file with a key stored next to it (security theatre). |
| D7 | **Precedence: flags > environment > `.env` > user config > defaults.** API keys: environment > `.env` > credential store. | The user config is a machine-wide default. Environment variables and `.env` are explicit and narrower in scope (a session or a project), so they must win. CI and other env-only setups behave exactly as before because they have no user config. This matches how `gh`, `aws` and `git -c` treat files versus the environment. | Config above the environment: a saved provider would silently override `GITCOMMIT_AI_PROVIDER` in CI, which breaks requirement 8. Config above `.env`: a personal default would override a project's `.env`. |
| D8 | **The model is saved per provider** (`[providers.<name>].model`). The effective model is: `--model` > `GITCOMMIT_AI_MODEL` (env, then `.env`) > `providers.<effective provider>.model` > the provider's built-in default. | Switching providers and back restores the model you had. A saved model never gets sent to the wrong provider when the environment changes the provider. | One global `model` key (switching providers would leave an invalid model). |
| D9 | **First-run condition.** The wizard starts only when every one of these holds: (a) the command needs an LLM (`generate`, or bare `git ai-commit`); (b) no `config.toml` exists; (c) neither the environment nor `.env` sets `GITCOMMIT_AI_PROVIDER` or any provider key variable (§5); (d) the session is interactive (§4.6). | Existing env-var users never see the wizard. Today, a run with none of these set fails with `MissingAPIKeyError` anyway, so the wizard only replaces an error. | Starting whenever `config.toml` is missing (would ambush existing env users). Starting on any command (`config` and `hook install` don't need a provider). |
| D10 | **"Interactive" means**: stdin and stdout are TTYs, `TERM` is not `dumb`, and `CI` is unset or empty. The hook is never interactive, regardless of TTYs. | Git runs `prepare-commit-msg` with a TTY in some setups, so TTY detection alone is not enough there. Requiring stdout to be a TTY keeps menus out of `git ai-commit --dry-run \| pbcopy`. | Checking stdin only (menus would corrupt piped stdout). |
| D11 | **Commit modes**: `preview`, `confirm` (default) and `auto`, stored as `commit_mode` and settable with `GITCOMMIT_AI_COMMIT_MODE`. Per-run flags win: `--dry-run` forces preview, `--yes` forces auto, and the new `--confirm` forces asking. `--yes` together with `--confirm` is a usage error. | This matches the requirement. `--confirm` gives `auto` users a way to review a single commit. The env variable keeps the "every setting has a variable" rule the docs checker enforces. | No `--confirm` flag (an `auto` user would have to edit config to review once). |
| D12 | **`confirm` mode never prompts without a terminal.** In a non-interactive session it fails: "Not committing: there is no terminal to confirm in. Pass --yes, or --dry-run to print only." `auto` is treated exactly like `--yes`, including the `--accept-partial` guard on interrupted runs. | Requirement 7. **Behaviour change**: `echo y \| git ai-commit` used to work through `typer.confirm` reading stdin, and now fails with a message that points to `--yes`. This goes in the changelog. | Keep reading confirmation from piped stdin (contradicts "never prompt"). |
| D13 | **Command names**: `setup`, `provider`, `model [NAME]`, `key [--provider P] [--stdin]`, `mode [MODE]`, `config` (shows settings, as today) with `config get/set/unset/path`, `reset [--yes]` and `doctor`. They are registered on both `gitcommit-ai` and `git ai-commit`. | Short verbs, like `gh`. `git ai-commit config` keeps working unchanged because the group's callback shows settings when no subcommand is given. Each interactive command also has a non-interactive form, such as `model NAME` or `key --stdin`. | One `configure` command with subflags (harder to discover). A nested `settings provider ...` namespace (more typing for frequent actions). |
| D14 | **Keys can't be set through `config set`**, because values passed as arguments end up in shell history. Keys go through `key`, which reads hidden input, or `key --stdin` for scripts. | Secrets stay out of `ps` output and shell history. | `config set api_key ...`. |
| D15 | **The wizard validates in two steps.** First a cheap auth call per provider (§5). After a model is chosen, it sends one tiny completion (the *probe*: "Reply with OK", at most 16 output tokens). The probe counts as passed on any 2xx response, even an empty one. | The auth call catches bad keys before the user scrolls a model list. The probe catches what listing can't: models that need another endpoint (for example OpenAI Responses-only models), keys with no inference permission (Hugging Face fine-grained tokens), and Ollama Cloud keys, whose listing endpoint is public (verified: a bad key still gets 200). It costs a fraction of a cent once per setup. | Probe only (users would scroll a list with a dead key). Auth only (a model can be picked and then fail on first use). |
| D16 | **Nothing is written until the wizard finishes.** Writes go to temporary files in the same directory, followed by `os.replace`. Credentials are written first and `config.toml` last, so the presence of `config.toml` means setup is complete. | Ctrl+C or a crash at any point leaves the previous state untouched, or at worst an orphaned saved key, which the next wizard run offers to reuse. | Writing each answer as it is given. |
| D17 | **New package `git_commit_ai.onboarding`** (wizard, prompt adapters, model menus). It sits between `cli` and `llm`/`core` and must not import `agents`, `context` or `git`. Config and credential storage live in `core`, which stays Git-free. Model catalogues live in `llm`. | This keeps the one-way dependency rule. The wizard is pure logic over a `Prompter` and a catalogue, which keeps it testable without a TTY. | Putting the wizard in `cli` (mixes Typer I/O with flow logic and makes it hard to test). Putting storage in `cli` (`core.settings` needs it for precedence). |
| D18 | **Keep `[openai]`, `[anthropic]`, `[gemini]`, `[ollama]` and `[all]` as empty extras** for one release, so existing install commands don't warn. Add `[keyring]`. Remove the empty extras in the release after. | Old READMEs, blog posts and CI scripts keep working. | Remove them at once (pip warns "does not provide the extra"; not fatal, but noisy). |

---

## 2. UX flows

Transcripts show what the user sees. `❯` marks the highlighted menu row. `(•••)` is hidden
input. Menu prompts end with the instruction questionary prints: `(Use shortcuts or arrow keys)`.
Every menu row is numbered, and pressing a number moves to that row. Status lines go to stderr.
The generated commit message alone goes to stdout.

### 2.1 First run (hosted provider: Anthropic), then generation continues

```text
$ git add -p && git ai-commit
Welcome to GitCommit AI. Let's set up a model provider (about a minute).
Your answers are saved to ~/.config/gitcommit-ai/config.toml; change them later
with `git ai-commit setup`.

? Which provider do you want to use? (Use shortcuts or arrow keys)
   1) Anthropic          Claude models · API key
   2) Google Gemini      Gemini models · API key
 ❯ 3) OpenAI             GPT models · API key
   4) OpenRouter         Hundreds of models from many vendors · API key
   5) Hugging Face       Open models via Inference Providers · access token
   6) Ollama             Run models on this computer or your own server · no key for local
> 1

Create a key at https://console.anthropic.com/settings/keys
? Anthropic API key: (•••••••••••••••••••)
Checking the key... ok

Fetching the models available to this key... 9 models
? Which model? (Use shortcuts or arrow keys)
 ❯ 1) Claude Sonnet 5.5     claude-sonnet-5-5
   2) Claude Opus 5.5       claude-opus-5-5
   3) Claude Haiku 5.5      claude-haiku-5-5
   ...
   8) Show all 9 models
   9) Type a model name
> 1
Sending a test request to claude-sonnet-5-5... ok

? When a message is ready, should GitCommit AI commit it? (Use shortcuts or arrow keys)
 ❯ 1) Ask me first (recommended)       confirm
   2) Only print the message           preview
   3) Commit without asking            auto
> 1

Saved. Provider: anthropic · model: claude-sonnet-5-5 · commits: ask first
Key stored in ~/.config/gitcommit-ai/credentials.toml (readable only by you).
Change any of this later: git ai-commit provider | model | key | mode | config

Generating a commit message with anthropic/claude-sonnet-5-5...
feat(orders): add refund endpoint
? Commit with this message? [y/N]: y
Committed 3f9c2a1
```

Notes:
- The provider menu's default row is OpenAI because `openai` is today's default provider.
  The rows are in alphabetical order with Ollama last, and there is no "recommended" badge, so
  no single vendor is favoured.
- The shortlist holds up to 7 models from the provider's own order (§5), then **Show all N
  models** (a type-to-filter list), then **Type a model name**. When the provider returns 7
  models or fewer, the menu lists them all and drops "Show all".
- If the `keyring` extra is installed and a usable backend exists, one extra question appears
  before saving: `Where should the key be stored? 1) This computer's keychain 2) A private file
  in ~/.config/gitcommit-ai`. Otherwise it is skipped.
- **If the key variable is already set** (for example, `ANTHROPIC_API_KEY` is in the
  environment but `GITCOMMIT_AI_PROVIDER` is not), the key step changes, as shown below. In
  this case the wizard was started by `setup`, since D9 doesn't trigger it.

  ```text
  Found ANTHROPIC_API_KEY in your environment.
  ? Use it? It will not be saved, so it must stay set. (Y/n) y
  Checking the key... ok
  ```

### 2.2 Bad key, then retry

```text
? OpenAI API key: (•••)
Checking the key... OpenAI rejected this key (HTTP 401).
? What now? (Use shortcuts or arrow keys)
 ❯ 1) Enter the key again
   2) Choose a different provider
   3) Cancel setup
```

The message never echoes the key or the provider's error body. OpenAI's 401 body contains the
key (verified).

### 2.3 Hosted provider whose model listing fails (Gemini, network or 5xx)

```text
? Gemini API key: (•••)
Checking the key... ok
Fetching the models available to this key... failed (timed out after 10s).
These suggestions were current in November 2026 and may be out of date:
? Which model? (Use shortcuts or arrow keys)
 ❯ 1) gemini-2.5-flash
   2) gemini-2.5-pro
   3) Try fetching the list again
   4) Type a model name
```

For Gemini, the auth check and the listing are the same request (§5), so the fallback appears
there only when the key check passed and a later page failed, or when the first call timed
out. In that case the key step reports `couldn't check the key (timed out); continuing`, and
the probe becomes the real check.

### 2.4 OpenRouter (large catalogue, filtering)

```text
? OpenRouter API key: (•••)
Checking the key... ok
Fetching models... 437 text models
? Which model? (Use shortcuts or arrow keys)
 ❯ 1) Anthropic: Claude Sonnet 5.5     anthropic/claude-sonnet-5.5
   ...
   8) Show all 437 models
   9) Type a model name
> 8
? Which model? (Use arrow keys, type to filter)
  qwen3█
 ❯ Qwen: Qwen3.8 Flash                 qwen/qwen3.8-flash
   Qwen: Qwen3.8 27B                   qwen/qwen3.8-27b       free
   ...
```

### 2.5 Hugging Face

```text
? Hugging Face access token: (•••)
  Create a fine-grained token with "Make calls to Inference Providers" at
  https://huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained
Checking the token... ok (user: alice)
Fetching models served by Inference Providers... 132 models
? Which model? ...
> 1
Sending a test request to deepseek-ai/DeepSeek-V4.1-Flash... failed: this token may not call
Inference Providers (HTTP 403).
? What now?
 ❯ 1) Enter a different token
   2) Choose a different model
   3) Choose a different provider
   4) Cancel setup
```

### 2.6 Ollama, local, with models

```text
? Which provider do you want to use? > 6
? Where does Ollama run? (Use shortcuts or arrow keys)
 ❯ 1) On this computer          http://localhost:11434
   2) Ollama Cloud              https://ollama.com · API key
   3) Another server            enter its URL
> 1
Looking for Ollama at http://localhost:11434... found (version 0.40.12)
? Which model? (Use shortcuts or arrow keys)
 ❯ 1) gemma3:4b          4.3B · Q4_K_M · 3.3 GB
   2) Type a model name
   (1 embedding-only model hidden: nomic-embed-text:latest)
> 1
Sending a test request to gemma3:4b... ok
```

### 2.7 Ollama, local, no models pulled

```text
Looking for Ollama at http://localhost:11434... found (version 0.40.12)
No chat models are pulled yet. Pull one in another terminal, for example:
    ollama pull <model>          browse models at https://ollama.com/library
? What now? (Use shortcuts or arrow keys)
 ❯ 1) I've pulled a model; check again
   2) Type a model name
   3) Choose a different provider
   4) Cancel setup
```

### 2.8 Ollama, local, server not running

```text
Looking for Ollama at http://localhost:11434... not reachable (connection refused).
Install Ollama from https://ollama.com/download, then start it with `ollama serve`
(or open the Ollama app).
? What now? (Use shortcuts or arrow keys)
 ❯ 1) Check again
   2) Use a different URL
   3) Choose a different provider
   4) Cancel setup
```

### 2.9 Ollama Cloud

```text
? Where does Ollama run? > 2
Create a key at https://ollama.com/settings/keys
? Ollama API key: (•••)
Fetching models from https://ollama.com... 31 models
? Which model? > 1
Sending a test request to deepseek-v4.1-flash... ok
```

`https://ollama.com/api/tags` is public, so a bad key passes listing (verified). The probe is
the key check here. On HTTP 401 the wizard offers to re-enter the key.

### 2.10 Ollama on another server

```text
? Where does Ollama run? > 3
? Ollama URL: https://ollama.internal.example:11434
Looking for Ollama at https://ollama.internal.example:11434... found (version 0.39.2)
? Which model? ...
```

If `/api/version` answers 401 or 403, the wizard asks `? This server needs a key. Key:
(•••)`, sends it as a Bearer token, and retries. The URL is normalised: a trailing `/` is
stripped, a missing scheme is rejected with a hint, and `http://` to a non-loopback host gets a
one-line warning that requests are unencrypted.

### 2.11 Change model

```text
$ git ai-commit model
Current: anthropic / claude-sonnet-5-5
Fetching the models available to this key... 9 models
? Which model? ...
> 3
Sending a test request to claude-haiku-5-5... ok
Saved. Model for anthropic: claude-haiku-5-5

$ git ai-commit model claude-opus-5-5          # non-interactive form: no listing, no probe
Saved. Model for anthropic: claude-opus-5-5 (run `git ai-commit doctor` to test it)
```

If `GITCOMMIT_AI_MODEL` is set in the environment or `.env`, both forms save anyway and then
warn: `GITCOMMIT_AI_MODEL=... in your environment overrides this setting.`

### 2.12 Change provider (other keys kept)

```text
$ git ai-commit provider
Current: anthropic / claude-sonnet-5-5
? Which provider do you want to use? > 4
A key for OpenRouter is already saved (••••••••3f2a).
? Use it? (Y/n) y
Checking the key... ok
? Which model? ...
Saved. Provider: openrouter · model: anthropic/claude-sonnet-5.5
Your Anthropic key is still saved; switch back with `git ai-commit provider`.
```

Switching back to a provider that already has a saved model shows that model as the default
row in the model menu.

### 2.13 Update a key

```text
$ git ai-commit key
? New API key for anthropic: (•••)
Checking the key... ok
Saved.

$ printf %s "$NEW_KEY" | git ai-commit key --provider openrouter --stdin
Checking the key... ok
Saved.
```

### 2.14 Change commit mode

```text
$ git ai-commit mode
Current: confirm
? When a message is ready, should GitCommit AI commit it? > 3
Commit without asking? Messages will be committed as soon as they're generated. (y/N) y
Saved. Commit mode: auto (override once with --confirm or --dry-run)

$ git ai-commit mode preview
Saved. Commit mode: preview
```

### 2.15 Reset

```text
$ git ai-commit reset
This removes:
  ~/.config/gitcommit-ai/config.toml
  ~/.config/gitcommit-ai/credentials.toml (keys for: anthropic, openrouter)
Environment variables and .env files are not touched.
? Remove all saved settings and keys? (y/N) y
Removed. Run `git ai-commit` to set up again.
```

Without a TTY, `reset` requires `--yes` and otherwise exits 1 with `reset needs --yes when
there is no terminal to confirm in.`

### 2.16 Non-interactive with no configuration (CI, a pipe, or the hook)

```text
$ git ai-commit --dry-run | cat
Error: No provider is set up. Run `git ai-commit setup` in a terminal, or set
GITCOMMIT_AI_PROVIDER and that provider's key variable (for example ANTHROPIC_API_KEY).
```

The hook prints `git-ai-commit: no proposal (no provider is set up; run git ai-commit setup)`
and exits 0, as all hook failures do today.

### 2.17 Doctor

```text
$ git ai-commit doctor
Provider   anthropic                    [config file]
Model      claude-sonnet-5-5            [config file]
API key    ••••••••a91c                 [credentials file]
Config     ~/.config/gitcommit-ai/config.toml
Key check            ok (HTTP 200, 0.3s)
Model listed         ok
Test request         ok (0.9s)
```

Each failing line says what to run. Doctor exits 1 if any check fails and never prompts. It
does not spend tokens unless the test request runs; pass `--no-request` to skip it.

---

## 3. Config schema, storage layout and precedence

### 3.1 Files

| Path | Contents | Mode |
|---|---|---|
| `<dir>/config.toml` | Settings (no secrets) | 0644 |
| `<dir>/credentials.toml` | Keys, only when `credential_store = "file"` | 0600; directory 0700 |
| OS keyring, service `gitcommit-ai`, username `<provider>` | Keys, when `credential_store = "keyring"` | Managed by the OS |

`<dir>` resolution, in `core/paths.py`:
1. `GITCOMMIT_AI_CONFIG_DIR` if it is set and non-empty.
2. Windows: `%APPDATA%\gitcommit-ai`.
3. Otherwise `$XDG_CONFIG_HOME/gitcommit-ai` if `XDG_CONFIG_HOME` is absolute, else
   `~/.config/gitcommit-ai`.

### 3.2 `config.toml`

```toml
schema_version = 1
provider = "anthropic"            # ProviderName
commit_mode = "confirm"           # preview | confirm | auto
credential_store = "file"         # file | keyring

[providers.anthropic]
model = "claude-sonnet-5-5"

[providers.openrouter]
model = "anthropic/claude-sonnet-5.5"

[providers.ollama]
model = "gemma3:4b"
base_url = "http://localhost:11434"

[generation]                      # written only by `config set`
temperature = 0.3
timeout = 60.0
max_iterations = 3
max_diff_chars = 24000
max_retries = 3
retry_base_delay = 1.0
retry_max_delay = 30.0
```

Pydantic model (`core/user_config.py`):

```python
SCHEMA_VERSION = 1


class CommitMode(enum.StrEnum):
    PREVIEW = "preview"
    CONFIRM = "confirm"
    AUTO = "auto"


class CredentialStoreKind(enum.StrEnum):
    FILE = "file"
    KEYRING = "keyring"


class ProviderPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    model: str | None = None
    base_url: str | None = None  # meaningful for ollama only


class GenerationOverrides(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    temperature: float | None = None
    timeout: float | None = None
    max_iterations: int | None = None
    max_diff_chars: int | None = None
    max_retries: int | None = None
    retry_base_delay: float | None = None
    retry_max_delay: float | None = None


class UserConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    schema_version: int = SCHEMA_VERSION
    provider: ProviderName | None = None
    commit_mode: CommitMode | None = None
    credential_store: CredentialStoreKind = CredentialStoreKind.FILE
    providers: dict[ProviderName, ProviderPreferences] = {}
    generation: GenerationOverrides = GenerationOverrides()
```

Range validation is not duplicated here. The merged values are validated by
`GitCommitAIConfig`, and its error names the config key (`config.toml: temperature: ...`),
exactly as `from_env` names the variable today.

### 3.3 `credentials.toml`

```toml
schema_version = 1
[keys]
anthropic = "sk-ant-..."
openrouter = "sk-or-..."
ollama = "..."        # only for Ollama Cloud or an authenticated server
```

### 3.4 Keys for `config get/set/unset`

Each key maps one-to-one to the existing variable: key `x` ⇔ `GITCOMMIT_AI_X`. Users only
need to learn one naming scheme.

| Key | Stored at | Variable |
|---|---|---|
| `provider` | `provider` | `GITCOMMIT_AI_PROVIDER` |
| `model` | `providers.<active>.model` | `GITCOMMIT_AI_MODEL` |
| `commit_mode` | `commit_mode` | `GITCOMMIT_AI_COMMIT_MODE` (new) |
| `ollama_url` | `providers.ollama.base_url` | `GITCOMMIT_AI_OLLAMA_URL` |
| `temperature`, `timeout`, `max_iterations`, `max_diff_chars`, `max_retries`, `retry_base_delay`, `retry_max_delay` | `generation.*` | existing `GITCOMMIT_AI_*` |
| `credential_store` | `credential_store` | none (switching migrates the keys, §4.3) |

`model --provider P` and `config set model X --provider P` address a provider other than the
active one. Unknown keys exit 1 and list the valid ones. `api_key`, `key` or anything else
ending in `_key` gets: `API keys aren't set with config; run git ai-commit key.`

### 3.5 Precedence

```text
settings:  --flag  >  environment  >  .env (cwd, then repo root)  >  config.toml  >  built-in default
model:     --model >  GITCOMMIT_AI_MODEL (env, .env)  >  providers.<effective provider>.model  >  provider default
API key:   <provider key variable> (env, then .env)  >  credential store
commit:    --dry-run / --yes / --confirm  >  GITCOMMIT_AI_COMMIT_MODE (env, .env)  >  commit_mode  >  confirm
```

`SettingSource` gains `USER_CONFIG = "config file"` and `CREDENTIAL_STORE` (shown as
`credentials file` or `keychain`). `config` prints a source for every row.

### 3.6 Version and corruption handling

| Situation | Behaviour |
|---|---|
| `config.toml` is invalid TOML | `ConfigurationError: ~/.config/gitcommit-ai/config.toml is not valid TOML (line 4, column 9). Fix it or run git ai-commit reset.` The wizard does not run over it, and nothing is overwritten. |
| A value is out of range or of the wrong type | The same error, naming the key. |
| `schema_version` > supported | Read the keys this version knows, warn once per run on stderr (`config.toml was written by a newer gitcommit-ai; some settings may be ignored`), and **refuse every write** (`setup`, `set`, `model`, …) with `upgrade gitcommit-ai, or run git ai-commit reset`. `reset` still works. |
| `schema_version` < supported (future) | Migrate in memory. Write the migrated file only on the next deliberate write. |
| Unknown keys at the current version | Ignored. `doctor` lists them as a warning. |
| `credentials.toml` mode is group- or world-readable (POSIX) | Warn on stderr (`credentials.toml is readable by other users; run chmod 600 <path>`) and still use it. The next write fixes the mode. |

---

## 4. Module and interface design

### 4.1 Dependency direction after this phase

```text
cli → onboarding → llm → core
cli → agents → context → core
cli → git → core
agents → llm
```

`onboarding` may import `llm` and `core` only. `tests/unit/test_layers.py` gains
`"onboarding": {"core", "llm"}`, and `cli` gains `"onboarding"`. The reduced edge set (and both
layer diagrams that `scripts/check_docs.py` compares against it) gains `("cli", "onboarding")`
and `("onboarding", "llm")`. `onboarding → core` is implied, so it is not drawn.

### 4.2 New and changed modules

```text
src/git_commit_ai/
  core/
    paths.py            NEW  config_dir(env) -> Path
    toml_writer.py      NEW  dumps(data) -> str  (restricted types, round-trip checked)
    user_config.py      NEW  UserConfig, CommitMode, load_user_config, save_user_config
    credentials.py      NEW  CredentialStore protocol, FileCredentialStore, KeyringCredentialStore,
                             open_credential_store, ResolvedKey
    settings.py         CHG  user-config layer, api_key_for(), commit_mode, new sources
    config.py           CHG  ProviderName += OPENROUTER, HUGGINGFACE; commit_mode field;
                             ENV_FIELDS += GITCOMMIT_AI_COMMIT_MODE
    exceptions.py       CHG  + AuthenticationError, ModelNotAvailableError, SetupCancelledError,
                             SetupRequiredError, UnsupportedConfigVersionError;
                             MissingAPIKeyError wording; - ProviderNotInstalledError
  llm/
    http.py             NEW  HttpClient, map_http_error, redact
    registry.py         NEW  ProviderSpec table (replaces factory._BackendSpec)
    openai_compatible.py NEW  OpenAICompatibleBackend (openai, openrouter, huggingface)
    anthropic_provider.py CHG  rewritten on HttpClient
    gemini_provider.py  CHG  rewritten on HttpClient
    ollama_provider.py  CHG  rewritten on HttpClient (+ optional bearer key)
    openai_provider.py  DEL  (replaced by openai_compatible.py)
    catalog.py          NEW  ModelInfo, ModelCatalog protocol, one catalogue per provider,
                             KeyCheck, ProbeResult, OllamaServer
    factory.py          CHG  create_provider(config, api_key, ...)
  onboarding/           NEW
    __init__.py
    prompts.py          Prompter protocol, Choice, QuestionaryPrompter, PlainPrompter,
                        make_prompter
    terminal.py         is_interactive(env, stdin, stdout)
    model_menu.py       shortlist(), menu choices for a model list
    wizard.py           SetupWizard and its steps; SetupResult; persist()
    ollama_flow.py      local/cloud/server sub-flow
  cli/
    interaction.py      NEW  Interaction (prompter factory, transport, interactive flag) on ctx.obj
    setup_cmds.py       NEW  setup, provider, model, key, mode, reset, doctor
    config_cmd.py       CHG  becomes a Typer group: callback = show; get/set/unset/path
    generate.py         CHG  first-run trigger, commit-mode resolution, --confirm
    hook.py             CHG  never interactive; SetupRequiredError → "no proposal" note
    main.py, git_subcommand.py CHG  register the new commands on both apps
    settings_loader.py  CHG  passes the user config and credential store
```

### 4.3 Key signatures

```python
# core/paths.py
CONFIG_DIR_VARIABLE = "GITCOMMIT_AI_CONFIG_DIR"


def config_dir(env: Mapping[str, str], platform: str = sys.platform) -> Path: ...


# core/toml_writer.py
TomlValue = str | int | float | bool


def dumps(
    data: Mapping[str, TomlValue | Mapping[str, TomlValue | Mapping[str, TomlValue]]],
) -> str:
    """Raise TypeError for any other type. Strings are written as TOML basic strings with escapes."""


# core/user_config.py
CONFIG_FILE = "config.toml"


def load_user_config(directory: Path) -> UserConfig | None: ...  # None when absent
def save_user_config(directory: Path, config: UserConfig) -> Path: ...  # atomic; refuses newer
def atomic_write(path: Path, text: str, mode: int) -> None: ...  # tmp in same dir + os.replace


# core/credentials.py
class CredentialStore(Protocol):
    kind: CredentialStoreKind

    def get(self, provider: ProviderName) -> str | None: ...
    def set(self, provider: ProviderName, key: str) -> None: ...
    def delete(self, provider: ProviderName) -> None: ...
    def saved_providers(self) -> list[ProviderName]: ...


class FileCredentialStore: ...  # credentials.toml, 0600


class KeyringCredentialStore: ...  # imports keyring lazily; raises ConfigurationError if unusable


def keyring_available() -> bool: ...  # importable, and backend is not fail.Keyring / null.Keyring
def open_credential_store(directory: Path, kind: CredentialStoreKind) -> CredentialStore: ...


@dataclass(frozen=True)
class ResolvedKey:
    value: str = field(repr=False)
    source: SettingSource  # ENVIRONMENT | DOTENV | CREDENTIAL_STORE
    variable: str | None  # which env var, when from env/.env


# core/settings.py
def load_settings(
    env: Mapping[str, str],
    start_dir: Path | None = None,
    repo_root: Path | None = None,
    user_config: UserConfig | None = None,
    credentials: CredentialStore | None = None,
) -> Settings: ...


class Settings:  # existing fields plus:
    user_config: UserConfig | None
    credentials: CredentialStore | None

    def api_key_for(self, provider: ProviderName) -> ResolvedKey | None: ...
    def provider_configured_by_environment(self) -> bool: ...  # D9 (c)
    @property
    def commit_mode(self) -> CommitMode: ...


# llm/registry.py
@dataclass(frozen=True)
class ProviderSpec:
    name: ProviderName
    label: str  # "Google Gemini"
    blurb: str  # menu hint
    key_variables: tuple[str, ...]  # first is canonical; () for ollama
    key_url: str | None
    base_url: str
    default_model: str
    fallback_models: tuple[str, ...]
    backend: Literal["openai_compatible", "anthropic", "gemini", "ollama"]


FALLBACKS_AS_OF = "November 2026"
PROVIDERS: Mapping[ProviderName, ProviderSpec]


def spec(provider: ProviderName) -> ProviderSpec: ...


# llm/http.py
class HttpClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        headers: Mapping[str, str],
        timeout: float,
        transport: httpx.BaseTransport | None = None,
        secrets: Sequence[str] = (),
    ) -> None: ...
    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any: ...
    def post_json(self, path: str, body: Mapping[str, Any]) -> Any: ...


def map_http_error(
    provider: str, response: httpx.Response, secrets: Sequence[str]
) -> LLMError: ...
def redact(text: str, secrets: Sequence[str]) -> str: ...


# llm/catalog.py
@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str  # display name, or id
    detail: str = ""  # "free", "4.3B · Q4_K_M · 3.3 GB", "deprecated"
    created: int | None = None
    deprecated: bool = False


class KeyCheck(enum.Enum):
    VALID, INVALID, UNKNOWN  # UNKNOWN: timeout or 5xx; continue


class ModelCatalog(Protocol):
    def check_key(self) -> KeyCheck: ...
    def list_models(self) -> list[ModelInfo]: ...  # filtered and sorted; raises LLMError
    def probe(
        self, model: str
    ) -> None: ...  # raises AuthenticationError / ModelNotAvailableError / LLMError


def catalog_for(
    provider: ProviderName,
    api_key: str | None,
    base_url: str,
    transport: httpx.BaseTransport | None = None,
) -> ModelCatalog: ...


@dataclass(frozen=True)
class OllamaServer:
    reachable: bool
    version: str | None
    needs_key: bool
    error: str | None


def detect_ollama(
    base_url: str, api_key: str | None, transport: httpx.BaseTransport | None = None
) -> OllamaServer: ...


# llm/factory.py
def create_provider(
    config: GitCommitAIConfig,
    api_key: str | None,
    on_retry: Callable[[RetryEvent], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> LLMProvider: ...  # raises MissingAPIKeyError when the spec needs a key and none is given


# onboarding/prompts.py
T = TypeVar("T")


@dataclass(frozen=True)
class Choice(Generic[T]):
    value: T
    label: str
    hint: str = ""


class Prompter(Protocol):
    def select(
        self,
        message: str,
        choices: Sequence[Choice[T]],
        default: T | None = None,
        searchable: bool = False,
    ) -> T: ...
    def secret(self, message: str) -> str: ...
    def text(
        self,
        message: str,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str: ...
    def confirm(self, message: str, default: bool = False) -> bool: ...
    def note(self, message: str) -> None: ...  # stderr
    def status(self, message: str) -> ContextManager[Callable[[str], None]]:
        ...
        # prints "message... " then the result text passed to the callback


# Every method raises SetupCancelledError on Ctrl+C, Ctrl+D or EOF.


class QuestionaryPrompter: ...  # unsafe_ask(); use_shortcuts=True when len(choices) <= 36 and not searchable


class PlainPrompter: ...  # numbered list + input(); getpass for secrets; used when prompt_toolkit fails


def make_prompter() -> Prompter: ...  # tries QuestionaryPrompter; falls back to PlainPrompter


# onboarding/terminal.py
def is_interactive(env: Mapping[str, str], stdin: TextIO, stdout: TextIO) -> bool: ...


# onboarding/wizard.py
class Step(enum.Enum):
    PROVIDER, KEY, MODEL, MODE, STORE


@dataclass(frozen=True)
class SetupResult:
    provider: ProviderName
    model: str
    base_url: str | None
    key: str | None = field(repr=False)  # None: nothing to save (env key, or keyless Ollama)
    commit_mode: CommitMode
    credential_store: CredentialStoreKind


class SetupWizard:
    def __init__(
        self,
        prompter: Prompter,
        settings: Settings,
        catalogs: Callable[[ProviderName, str | None, str], ModelCatalog],
        ollama_detector: Callable[[str, str | None], OllamaServer],
        keyring_ok: bool,
    ) -> None: ...
    def run_full(self) -> SetupResult: ...
    def run_provider(self) -> SetupResult: ...  # provider → key → model; mode unchanged
    def run_model(self) -> SetupResult: ...  # model only, current provider
    def run_key(self, provider: ProviderName) -> SetupResult: ...
    def run_mode(self) -> SetupResult: ...


def persist(result: SetupResult, directory: Path, previous: UserConfig | None) -> UserConfig:
    """Write credentials first, then config.toml, both atomically. Keeps other providers'
    entries. Raises UnsupportedConfigVersionError when previous is from a newer version."""


# cli/interaction.py
@dataclass
class Interaction:
    interactive: bool
    prompter_factory: Callable[[], Prompter]
    transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport


def current(ctx: typer.Context) -> Interaction: ...  # ctx.obj, else a default built from sys.*
```

### 4.4 Generate flow changes (`cli/generate.py`)

1. Load settings (now including the user config and credential store).
2. If D9 holds and the session is interactive, run `SetupWizard.run_full()`, call `persist()`,
   then reload settings and continue. If D9 holds and the session is not interactive, raise
   `SetupRequiredError` (message in §2.16).
3. Resolve the key. If the provider needs one and none resolves:
   - interactive: `run_key()` for that provider (the only step), then persist and continue;
   - otherwise: `MissingAPIKeyError`, which names both fixes: `git ai-commit key` and the
     variable.
4. Resolve the effective commit action from the flags and `settings.commit_mode`:
   `PREVIEW` → print and return. `AUTO` → the same as `--yes`, including the interruption
   guard. `CONFIRM` → if not interactive, fail as D12 says; otherwise call
   `prompter.confirm`.
5. Map `AuthenticationError` to: `<Provider> rejected the API key from <source>. Run git
   ai-commit key to replace it` (or, when the source is the environment, `update
   <VARIABLE>`). Map `ModelNotAvailableError` to `<model> isn't available from <provider>
   (it may have been retired or renamed). Run git ai-commit model to choose another.`

### 4.5 HTTP error mapping (`llm/http.py`, used by every backend and catalogue)

| Response | Error |
|---|---|
| `httpx.TimeoutException`, `httpx.TransportError`, 408, 500–599, 529 | `TransientLLMError` (retried) |
| 429 | `RateLimitError(retry_after=parse_retry_after(headers))` |
| 401, 403; Gemini 400 with `error.details[].reason == "API_KEY_INVALID"` (verified) | `AuthenticationError(provider)`: fixed text, body ignored |
| 404; 400 whose message matches `model` + (`not found`, `does not exist`, `not supported`, `invalid model`) | `ModelNotAvailableError(provider, model)` |
| Other 4xx | `LLMError(f"{provider} request failed (HTTP {code}): {redact(message)}")`, using the provider's `error.message` field, truncated to 300 characters |
| 2xx with a missing or empty text | `LLMResponseError` (existing `require_text`) |

`ConnectionRefused` against Ollama keeps raising `OllamaUnreachableError`.

### 4.6 Backend specifics

- **OpenAI-compatible**: `POST {base}/chat/completions` with `{model, messages, temperature}`
  and `Authorization: Bearer`. If the response is HTTP 400 with `error.param == "temperature"`
  (OpenAI reasoning models reject a custom temperature), it resends once without
  `temperature` and remembers that for the rest of the instance's life. OpenRouter calls also
  send `HTTP-Referer: https://github.com/vaibhav-barman/gitcommit-ai` and `X-Title:
  GitCommit AI` (optional attribution headers; no secrets).
- **Anthropic**: `POST https://api.anthropic.com/v1/messages` with headers `x-api-key` and
  `anthropic-version: 2023-06-01`, and body `max_tokens: 1024`, `system`, `messages`,
  `temperature`. The text is the concatenation of the `content[].text` blocks.
- **Gemini**: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
  with header `x-goog-api-key` (the key never goes in a URL), and body `systemInstruction`,
  `contents`, `generationConfig.temperature`. The text is the joined
  `candidates[0].content.parts[].text`.
- **Ollama**: `POST {base}/api/chat` with `{model, messages, stream: false, options:
  {temperature}}`, plus `Authorization: Bearer` when a key is set.

The probe uses the same request shape with a 16-token cap: `max_completion_tokens` for OpenAI,
`max_tokens` for OpenRouter, Hugging Face and Anthropic, `generationConfig.maxOutputTokens` for
Gemini and `options.num_predict` for Ollama.

---

## 5. Provider table

All setup calls use a 10-second timeout and one retry on 429 when `retry-after` is 10 seconds
or less. Everything else falls through to the error paths in §2.

| | OpenAI | Anthropic | Google Gemini | OpenRouter | Hugging Face | Ollama |
|---|---|---|---|---|---|---|
| `ProviderName` | `openai` | `anthropic` | `gemini` | `openrouter` (new) | `huggingface` (new) | `ollama` |
| Base URL | `https://api.openai.com/v1` | `https://api.anthropic.com/v1` | `https://generativelanguage.googleapis.com/v1beta` | `https://openrouter.ai/api/v1` | `https://router.huggingface.co/v1` | local `http://localhost:11434`; cloud `https://ollama.com`; or a custom URL |
| Auth header | `Authorization: Bearer` | `x-api-key` + `anthropic-version: 2023-06-01` | `x-goog-api-key` | `Authorization: Bearer` | `Authorization: Bearer` | none locally; `Authorization: Bearer` for cloud and protected servers |
| Key variables (first is canonical) | `OPENAI_API_KEY` | `ANTHROPIC_API_KEY` | `GOOGLE_API_KEY`, `GEMINI_API_KEY` | `OPENROUTER_API_KEY` (new) | `HF_TOKEN` (new) | `OLLAMA_API_KEY` (new; the official name) |
| Key validation | `GET /models` (401 when bad, verified). The same call lists the models. | `GET /models?limit=1000` (401 when bad, verified). The same call lists the models. | `GET /models?pageSize=1000` (**400** `API_KEY_INVALID` when bad, verified). The same call lists the models. | `GET /key` (401 when bad, verified; `/models` is public) | `GET https://huggingface.co/api/whoami-v2` (401 when bad, verified; `/v1/models` is public and returns 200 even with a bad token, verified) | none: `/api/tags` is public on ollama.com (verified). Local needs no key. Validated by the probe. |
| Model list | the same call | the same call; follow `after_id = last_id` while `has_more` | the same call; follow `nextPageToken` | `GET /models` (returns everything; no pagination needed) | `GET /v1/models` | `GET {base}/api/tags`; reachability via `GET {base}/api/version` (verified locally, `{"version":"0.40.12"}`) |
| Filter rules | Keep ids matching `^(gpt-\|o\d\|chatgpt-)`. Drop ids containing `embedding`, `tts`, `whisper`, `dall-e`, `image`, `audio`, `realtime`, `transcribe`, `search`, `moderation`, `instruct` or `codex`. Hide dated snapshots (`-\d{4}-\d{2}-\d{2}$`) from the shortlist but keep them under "Show all". The probe catches Responses-only models that slip through. | Keep everything returned. The default list already excludes `retired`; `lifecycle: deprecated` models get the detail `deprecated`. | Keep only models with `generateContent` in `supportedGenerationMethods`. Drop names containing `embedding`, `imagen`, `veo`, `-tts`, `image`, `native-audio`, `live` or `aqa`. The id is `name` minus `models/`. | Keep `"text" in architecture.output_modalities` and `"text" in input_modalities`. Drop ids ending in `:batch` (verified to exist), ids containing `guard` or `embed`, and models whose `expiration_date` is in the past. Detail `free` when `pricing.prompt == pricing.completion == "0"`. | Keep `"text" in architecture.output_modalities` and at least one `providers[].status == "live"`. Detail: provider count and the lowest `pricing.output` per 1M tokens, if present. | When an entry has `capabilities` (verified locally and on ollama.com), keep it only if `"completion"` is among them. Otherwise drop names or families containing `embed` or `bert`. Hidden embedding models are mentioned in a note. |
| Sort | `created`, newest first | API order, which is newest first (verified in the docs); deprecated last | stable models before `preview`/`exp`, then by numeric version descending (`gemini-(\d+(\.\d+)?)`), then name | `created`, newest first | `created`, newest first | `modified_at`, newest first |
| Shortlist (first 7) | non-dated aliases, newest first | active models, newest first | stable models, newest first | newest first; "Show all" is searchable | newest first; "Show all" is searchable | all pulled models (no shortlist under 10) |
| Fallback list, used only when listing fails | `gpt-5-mini`, `gpt-5` | `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5` | `gemini-2.5-flash`, `gemini-2.5-pro` | `anthropic/claude-sonnet-5.5`, `anthropic/claude-haiku-5.5` (live slugs, verified) | `deepseek-ai/DeepSeek-V4.1-Flash` (live, verified) | none: listing is local, so the wizard shows "pull a model" instead (§2.7) |
| Default model (env-only users, unchanged where it exists) | `gpt-4o-mini` | `claude-sonnet-5-5` | `gemini-2.5-flash` | `anthropic/claude-sonnet-5.5` | `deepseek-ai/DeepSeek-V4.1-Flash` | `llama3.1` |
| Key URL shown | `https://platform.openai.com/api-keys` | `https://console.anthropic.com/settings/keys` | `https://aistudio.google.com/apikey` | `https://openrouter.ai/settings/keys` | the token link in §2.5 | `https://ollama.com/settings/keys` (cloud only) |

Fallback lists are shown with the heading "These suggestions were current in
`FALLBACKS_AS_OF` and may be out of date". Before tagging a release, re-run each live listing
by hand and refresh the OpenAI and Gemini rows, which I could not list without keys.

---

## 6. Security checklist

- [ ] Keys are read only by `ResolvedKey` and held in fields declared with `repr=False`.
      `SetupResult.__repr__` and `ResolvedKey.__repr__` never show the key (a test asserts this).
- [ ] Keys never go in URLs. Gemini uses the `x-goog-api-key` header, not `?key=`, so they
      can't leak through httpx exception text, proxies or server logs.
- [ ] HTTP error mapping for 401 and 403 uses fixed text and ignores the response body.
      OpenAI echoes the submitted key in its 401 body (verified).
- [ ] `redact()` removes every known secret from every provider message before it reaches an
      exception, and also any token matching `(sk|hf|sk-or|sk-ant)-?[A-Za-z0-9_-]{8,}`.
- [ ] `config`, `config get`, `doctor` and the wizard show keys only through `mask_secret`
      (8 asterisks plus the last 4 characters).
- [ ] There is no `config set` path for keys (D14). `key --stdin` exists for scripts, so keys
      never appear in argv or shell history.
- [ ] The secret prompt is hidden: questionary `password`, or `getpass` in the plain fallback.
      The plain fallback warns when `getpass` falls back to echoing (`GetPassWarning`) and
      asks whether to continue.
- [ ] `credentials.toml` is created through `os.open(..., O_CREAT | O_EXCL | O_WRONLY, 0o600)`
      on a temporary file, then `os.replace`. The directory is created with mode 0700. On
      read, a mode wider than 0600 triggers a warning. On Windows the file inherits the
      per-user ACL of `%APPDATA%`, and this is documented.
- [ ] The config directory lives outside every repository. `GITCOMMIT_AI_CONFIG_DIR` pointing
      inside the current Git work tree draws a warning, so the user knows the keys could be
      committed.
- [ ] No logging of request headers or bodies anywhere. `--verbose` prints neither.
- [ ] `reset` deletes the keyring entries for every `ProviderName`, the credentials file and
      the config file, and reports exactly what it removed.
- [ ] The hook is never interactive, never touches the keyring when
      `credential_store = "file"`, and catches every exception (unchanged).
- [ ] An `http://` Ollama URL to a non-loopback host gets a warning whenever a key is sent
      to it.
- [ ] TLS verification is always on. There is no option to disable it.
- [ ] A test greps the whole repo for strings that look like real keys (in addition to the
      `.gitignore` for `.env`).

---

## 7. Test strategy

### 7.1 Global test setup (`tests/conftest.py`, autouse)

- Set `GITCOMMIT_AI_CONFIG_DIR` to `tmp_path / "config"`, so no test reads or writes the real
  config.
- Remove from the environment every provider key variable, plus `GITCOMMIT_AI_PROVIDER`,
  `GITCOMMIT_AI_MODEL`, `GITCOMMIT_AI_COMMIT_MODE` and `CI`.
- Guard the network: monkeypatch `httpx.HTTPTransport.handle_request` to raise
  `AssertionError("network access in a test")`. Tests always pass an `httpx.MockTransport`.
- `tests/fixtures/http.py`: `route(method, url) -> handler` helpers, canned payloads per
  provider (trimmed from the real responses captured in §5), and `RecordingTransport` to
  assert which calls were made, in which order, and with which headers.
- `tests/fixtures/prompter.py`: a `ScriptedPrompter(answers: list[object])` that returns
  answers in order, records every message and choice list, and raises `SetupCancelledError`
  when it meets the sentinel `CTRL_C`. It fails the test if answers are left over, or if a
  prompt arrives after the answers run out.

### 7.2 Tests per module

| Module | Tests |
|---|---|
| `core/paths.py` | override wins; empty override is ignored; Windows `APPDATA`; XDG absolute vs relative; macOS uses `~/.config` |
| `core/toml_writer.py` | round-trip with `tomllib` for every supported type; escapes (quotes, backslashes, newlines, unicode); nested table; rejects lists, None and datetimes; key quoting for non-bare keys; property test over random strings |
| `core/user_config.py` | load absent → None; load valid; invalid TOML → line and column in the message; bad value → key named; newer version read with warning; newer version refuses save; unknown keys ignored; atomic write leaves the old file when `os.replace` is patched to raise; file mode 0644 |
| `core/credentials.py` | file store set/get/delete/saved_providers; mode 0600 and directory 0700 (POSIX only); warning on 0644; other providers' keys preserved; keyring store against an in-memory fake keyring backend; `keyring_available` false for the fail backend and when not importable |
| `core/settings.py` | the full precedence matrix: flag > env > .env > config > default, for provider, model, commit mode, temperature and ollama URL; per-provider model chosen by the *effective* provider; env provider + config model for another provider → that provider's default; key precedence env > .env > store; `GEMINI_API_KEY` alias; `provider_configured_by_environment`; sources reported |
| `llm/http.py` | each row of the §4.5 table; 401 body containing the key never appears in the message; `redact`; retry-after parsing; Gemini `API_KEY_INVALID` → auth error |
| `llm/openai_compatible.py` | request shape and headers for each of the three providers; temperature fallback on `param: temperature`; empty choices → `LLMResponseError`; OpenRouter attribution headers |
| `llm/anthropic_provider.py`, `gemini_provider.py`, `ollama_provider.py` | request shape (system prompt placement, roles), header names, response parsing, error mapping, Ollama bearer key only when set |
| `llm/catalog.py` | for each provider: validation call and status → `KeyCheck`; pagination (Anthropic `after_id`, Gemini `nextPageToken`) with a 3-page fixture; every filter rule, with fixture entries for each excluded kind; sort order; detail strings; probe body caps; `detect_ollama` reachable / refused / 401 / old server without `capabilities` |
| `llm/registry.py` | every `ProviderName` has a spec; non-Ollama specs have at least one key variable and one fallback model; fallback models aren't empty strings |
| `llm/factory.py` | missing key → `MissingAPIKeyError` naming both fixes; Ollama with and without key; retry wrapping kept |
| `onboarding/terminal.py` | each of the four conditions in D10 independently |
| `onboarding/prompts.py` | `PlainPrompter` with `io.StringIO`: numbered selection, out-of-range retry, empty input picks the default, EOF → cancelled; `QuestionaryPrompter` driven through prompt_toolkit's `create_pipe_input` + `DummyOutput` for one select (arrow + Enter), one number shortcut, a searchable filter, a password and Ctrl+C; `make_prompter` falls back when prompt_toolkit raises at startup |
| `onboarding/model_menu.py` | shortlist size; "Show all" absent when there are 7 or fewer models; saved model is the default row; fallback heading when the listing failed |
| `onboarding/wizard.py` | the full happy path for each of the six providers; each transcript in §2 as a scripted test asserting the prompts seen and the `SetupResult`; bad key then retry; listing failure → fallback → typed name; probe failure branches; env key offered and not saved; saved key reused on provider switch; Ctrl+C at every step → `SetupCancelledError` and **no files written** (parametrised over steps); `persist` keeps other providers' models and keys; `persist` refuses a newer version |
| `onboarding/ollama_flow.py` | local found with models; none pulled → check again → found; unreachable → different URL; cloud with probe 401 → re-enter key; custom server needing a key; URL normalisation and the plain-HTTP warning |
| `cli` (integration, `CliRunner`, `Interaction` injected through `obj=`) | first run on a real temp repo: wizard → commit made; first run non-interactive → exit 1 with the §2.16 text; env-configured user never sees the wizard (precedence regression); saved config + missing key → key step only; `--confirm`/`--yes` conflict; each commit mode × each flag; `auto` + interrupted run still refuses without `--accept-partial`; `confirm` without a TTY → exit 1; every new command in its interactive and non-interactive form, on both `app` and `git_app`; `config` with no subcommand matches today's output plus the new rows; `config set` validation and secret refusal; `reset` with and without TTY / `--yes`; `doctor` pass and fail exit codes; auth failure message names the key's source |
| `cli/hook.py` | the hook never calls the prompter even when the TTY flags are true; no config → "no proposal" and exit 0; uses saved config and key |
| docs | `scripts/check_docs.py` already enforces that every new command, flag and variable appears in the README; extend its layer expectation; `test_layers.py` updated |

There are no live tests in CI. `scripts/smoke_providers.py`, an optional manual script that is
not run by pytest, runs `check_key`, `list_models` and `probe` for every provider whose key is
present in the environment, so a maintainer can refresh §5 before a release.

---

## 8. Implementation sequence

Each commit passes `ruff check`, `ruff format --check`, `mypy src tests scripts` (strict),
`pytest`, and `python -m scripts.check_docs`. That means every commit that adds a command, flag
or variable updates the README reference sections in the same commit. The final commit only
restructures the docs.

1. **`feat(config): add a per-user config file and credential store`**
   `core/paths.py`, `core/toml_writer.py`, `core/user_config.py`, `core/credentials.py`
   (file store, keyring store, lazy import), the `[keyring]` extra, the
   `GITCOMMIT_AI_CONFIG_DIR` variable, and the conftest isolation fixture. No command uses it
   yet.
   Tests: the paths, toml_writer, user_config and credentials rows.
2. **`feat(config): read saved settings and keys below the environment`**
   The new `load_settings` layer, per-provider model resolution, `api_key_for`, new
   `SettingSource` values, `settings_loader` wiring, and `config` output gaining the
   config-file and credential sources. `factory.create_provider` takes `api_key`, and the CLI
   passes `settings.api_key_for(...)`. Docs: precedence line in the README and `.env.example`.
   Tests: the precedence matrix, CLI `config` output.
3. **`refactor(llm): call providers over HTTP instead of vendor SDKs`**
   `llm/http.py`, `openai_compatible.py`, and the Anthropic, Gemini and Ollama rewrites.
   Delete `openai_provider.py` and the SDK imports. `httpx` becomes a dependency, and the
   extras become empty (D18). `ProviderNotInstalledError` goes, and
   `AuthenticationError`/`ModelNotAvailableError` arrive, with the network guard fixture.
   Replace the mocked-SDK tests with `MockTransport` tests. Update CONTRIBUTING's "Adding a
   provider" section and drop extras from the README's install line.
4. **`feat(llm): add OpenRouter and Hugging Face providers`**
   `ProviderName` values, `llm/registry.py` (replaces `_BackendSpec`), the key variables
   (`OPENROUTER_API_KEY`, `HF_TOKEN`, `OLLAMA_API_KEY`, `GEMINI_API_KEY` alias), and the Ollama
   bearer key. Update the README provider table and `.env.example`.
   Tests: backend shapes, registry, factory, `config` key rows.
5. **`feat(llm): list models and check keys for every provider`**
   `llm/catalog.py` with the filters, sorting, pagination, probes and `detect_ollama`, plus
   fixtures with trimmed real payloads.
   Tests: the catalog rows.
6. **`feat(onboarding): add the setup wizard and prompt adapters`**
   The `onboarding` package, the `questionary` dependency, `ScriptedPrompter`, and the layer
   test and both layer diagrams gaining `cli → onboarding → llm`. Nothing calls the wizard
   yet; it is exercised only by unit tests. That is fine for one commit because the next one
   wires it.
   Tests: the onboarding rows, including Ctrl+C at every step.
7. **`feat(cli): start setup on first run and add setup, provider, model and key`**
   `cli/interaction.py`, the first-run trigger in `generate`, the missing-key step,
   `SetupRequiredError`, `cli/setup_cmds.py` (`setup`, `provider`, `model`, `key`) on both
   apps, and the hook's non-interactive path. Add the new commands to the README reference.
   Tests: the CLI and hook rows for these commands.
8. **`feat(cli): add commit modes`**
   The `commit_mode` field, `GITCOMMIT_AI_COMMIT_MODE`, `--confirm`, the `mode` command, the
   wizard's mode step being saved, and D12 behaviour. CHANGELOG gets the `echo y |` behaviour
   change.
   Tests: the mode × flag matrix, `auto` with an interrupted run, `confirm` without a TTY.
9. **`feat(cli): add config get/set/unset/path, reset and doctor`**
   `config` becomes a group whose callback keeps today's output; add `reset` and `doctor`.
   Tests: these commands on both apps, secret refusal, newer-version refusal.
10. **`docs: make the quick start install, run, answer the prompts`**
    README restructure (§10), `docs/architecture.md` (new section on onboarding and storage,
    updated layer text), CHANGELOG summary, CONTRIBUTING setup line (`uv sync`, no
    `--all-extras` needed except for `keyring`).

---

## 9. Risks and open questions

### Risks (with mitigations)

| Risk | Mitigation |
|---|---|
| Rewriting four working backends causes regressions in error mapping. | Commit 3 keeps the existing behavioural tests (retry, rate limit, transient, auth), re-pointed at `MockTransport`, before anything new is added. The manual smoke script checks live behaviour before a release. |
| OpenAI's model naming changes, so the id filter keeps a model the chat endpoint rejects, or drops a new family. | The probe catches the first case at setup time with a clear "choose a different model" branch. "Type a model name" covers the second. The filter is data in one place. |
| Some newer OpenAI models reject `temperature`. | Automatic one-time resend without it (§4.6). |
| Fallback lists and default models go stale (the existing `gpt-4o-mini`, `gemini-2.5-flash` and `llama3.1` defaults may already be retired). | Fallbacks appear only when listing fails, and are labelled with a date. Defaults matter only to env-only users. `ModelNotAvailableError` tells them to run `git ai-commit model`. Refresh both as a release checklist item. |
| prompt_toolkit can't run in Git Bash / mintty on Windows without `winpty` (it raises `NoConsoleScreenBufferError`). | `make_prompter` catches the startup failure and uses `PlainPrompter` (numbered input). The README's Windows note recommends Windows Terminal. |
| `getpass` may echo in some Windows terminals. | `GetPassWarning` is detected; the user is warned and asked to confirm before typing a key, and `key --stdin` is offered. |
| Keyring dialogs block the hook. | Keyring is opt-in (D6), and the README says not to use it with the hook on macOS if dialogs appear. |
| Hugging Face's `whoami-v2` response shape for fine-grained scopes is not something I could verify without a token. | Validation relies only on its 200 vs 401 status. The inference permission is checked by the probe, whose 403 branch explains the needed scope (§2.5). |
| Ollama Cloud has no key-only check endpoint that I could verify. | The probe is the check (§2.9). |
| The `echo y \|` behaviour change (D12) could break someone's script. | Changelog entry, plus an error message that names `--yes`. |
| Anthropic's docs example now shows `Authorization: Bearer`, while `x-api-key` with `anthropic-version: 2023-06-01` is what I verified gets a proper 401. | Use `x-api-key`. The header names are constants in one module. |

### Open questions for you

1. **The PyPI name `gitcommit-ai` is already taken** by an unrelated project (version 0.1.1,
   `github.com/gagarinyury/gitcommit-ai`, "AI-powered git commit message generator with 7
   providers"). `git-ai-commit` is also taken. `git-commit-ai` and `gitcommitai` are free,
   but PyPI rejects names too similar to an existing one, and both normalise to
   `gitcommitai`, so they will very likely be refused. This decides the install command in
   the new quick start. Which name do you want to publish under? The import package
   `git_commit_ai` and the `git ai-commit` command can stay as they are.
2. **Should D12's change go in?** In `confirm` mode with no terminal, `echo y | git
   ai-commit` would stop working. I recommend making the change, because it follows your
   "never prompt without a TTY" requirement. But it does break a pattern that works today, so
   I want your explicit yes.

---

## 10. Documentation changes

- **README quick start** shrinks to three steps:
  ```bash
  pipx install <package>      # or: uv tool install <package> / pip install <package>
  git add -p
  git ai-commit               # first run asks for a provider, key, model and commit mode
  ```
  Then one sentence: "Change anything later with `git ai-commit setup`, or see
  [Configuration](#...)."
- **Providers table**: six rows with columns for what you need, where to get it, and its key
  variable. The `pip install '.[extra]'` column goes.
- **New reference section**, "Commands": `setup`, `provider`, `model`, `key`, `mode`, `config
  get/set/unset/path`, `reset`, `doctor`, each with its non-interactive form. Remember that
  `check_docs` requires every command and flag.
- **Configuration section**: storage locations per OS, the `config.toml` example, the
  precedence block from §3.5 (replacing "flags > environment > .env > defaults" everywhere,
  including `config_cmd.py`'s docstring and output), how keys are stored, and keyring opt-in.
- **"Using environment variables (CI, scripts)"** becomes a collapsible section. It keeps the
  existing variable table plus the new variables (`GITCOMMIT_AI_CONFIG_DIR`,
  `GITCOMMIT_AI_COMMIT_MODE`, `OPENROUTER_API_KEY`, `HF_TOKEN`, `OLLAMA_API_KEY`,
  `GEMINI_API_KEY`).
- **Commit modes**: a short table, how the flags override it, and the hook ignoring it.
- **Windows note**: Windows Terminal or PowerShell recommended, and the plain-menu fallback
  in Git Bash.
- **`.env.example`**: new variables, and a header that says most users don't need this file.
- **`docs/architecture.md`**: new "Onboarding and storage" section (wizard, prompt adapter,
  catalogues, HTTP backends, precedence), updated layer diagram and dependency text, and
  replacing "provider SDK" wording with HTTP.
- **CONTRIBUTING**: no `--all-extras` needed. "Adding a provider" now means adding a
  registry entry, a catalogue, and either a base URL on the OpenAI-compatible backend or a
  small native backend. Add the network-guard rule, `ScriptedPrompter` and `MockTransport`
  usage.
- **CHANGELOG (Unreleased)**: onboarding, two new providers, no SDK extras, commit modes, new
  commands, new precedence layer, the `echo y |` change, and removal of
  `ProviderNotInstalledError`.
- **Demo tape** (`docs/demo.tape`): add a first-run segment once the name in open question 1
  is settled.
