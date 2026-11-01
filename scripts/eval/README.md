# Evaluation harness

Measures how closely the messages GitCommit AI writes match a repository's **real** commit
style. It replays the repository's own recent commits through the full agent graph and scores
each result with deterministic checks. It is a developer tool, not part of the installed
package: it lives in `scripts/`, is type-checked and tested in CI, and is not shipped in the wheel.

## How it works

For each of the last N non-merge commits (newest first), the harness:

1. Reads the commit's diff **against its parent** straight from the object store
   (`git diff <parent> <commit>`; the empty tree for a root commit). Nothing is checked out and
   neither your index nor your working tree is touched.
2. Collects the history **older than that commit** (the usual history size, newest first), so the
   generator sees only what the real author could have seen. The commit itself, and anything
   after it, is never shown to the graph.
3. Runs the same agent graph that `generate` uses: diff analyst, context retrieval, generator,
   critic, reflection loop.
4. Compares the final message with the real one and records the critic's findings on the first
   draft and on the draft that was kept.

A commit with no file changes, or one where the provider fails before any draft exists, is
recorded with its error and left out of the percentages. It never stops the run.

## Running it

Run from the repository root of GitCommit AI, with the project environment active
(`uv sync --all-extras`):

```bash
# Offline: no network, no API key. Safe to try anywhere.
uv run python -m scripts.eval /path/to/repo --offline

# Live: the last 30 commits with your configured provider (default count is 30)
uv run python -m scripts.eval /path/to/repo --provider anthropic --model claude-sonnet-5-5

# Fewer commits, a different output folder, a tighter reflection cap
uv run python -m scripts.eval /path/to/repo -n 10 -o results/my-repo --max-iterations 2
```

| Option | Default | Meaning |
|---|---|---|
| `REPO` | required | Any local Git repository |
| `--count`, `-n` | `30` | How many of the newest non-merge commits to replay |
| `--provider` | `GITCOMMIT_AI_PROVIDER` or `openai` | `openai`, `anthropic`, `gemini`, or `ollama` |
| `--model`, `-m` | the provider's default | Model name |
| `--offline` | off | Replace the LLM with a deterministic stand-in; cannot be combined with `--provider` or `--model` |
| `--max-iterations` | `3` | Generate/critique rounds allowed per commit (1–10) |
| `--output-dir`, `-o` | `eval-output` | Where `report.json` and `report.md` are written |

Live runs read the same settings as the CLI (flags, then environment, then a `.env` in the
current directory), including API keys and the retry settings. Each commit costs one analyst call
plus, per round, one generator call and (once the rules pass) one faithfulness check: at most
`1 + 2 × max_iterations` calls, which is 7 at the default and usually 3 to 5. The harness states
that bound before it starts. Offline runs ignore the environment and any `.env`, so they are
reproducible.

The Markdown summary is printed to stdout and written to `report.md`; progress goes to stderr.
The exit code is 1 if the repository cannot be read or no commit could be evaluated.

## What offline mode is

`--offline` swaps the model for a rule-based stand-in (`offline.py`). Its first draft is
deliberately naive (`Updated <file>`), and later drafts follow the rules that the generator's own
prompt states. That exercises everything except a real model: replay, graph, critic, scoring and
reports, and it shows the reflection loop repairing a draft. **Its percentages say nothing about
any model.** The report says so on its first lines. Use offline mode to check the harness and to
see the output format; use a live run to measure a model.

## Reading the results

Agreement checks compare the generated message with the real commit. The length and mood checks
compare it with the repository's style profile, which is built from the commits older than the
one being replayed. Each is a pass or a fail.

| Metric (JSON key) | Passes when |
|---|---|
| `convention_match` | The generated and real commits use the same convention family: Conventional Commits (with or without a scope), gitmoji, bracket prefix, or plain |
| `subject_length_ok` | The description (after any prefix) is between 0.5× and 1.5× the profile's average subject length, at most 72 characters, with 50 always allowed as an upper bound. With no history, only the 72-character limit applies |
| `imperative_mood` | The first word of the description is a known base-form verb (the same test the style profile uses) |
| `capitalization_agrees` | The first letter of the description has the same case as the real commit's. Not applicable if either starts with a non-letter |
| `punctuation_agrees` | Both end with a period, or neither does |
| `scope_agrees` | Both have a scope, or neither does. **Only applicable when the real commit is a Conventional Commit** |
| `body_agrees` | Both have a body, or neither does |
| `critic_clean_before` | The first draft had no critic errors (warnings are allowed) |
| `critic_clean_after` | The kept draft has no critic errors |

An inapplicable check is excluded from the denominator rather than counted as a pass, which is why
the summary shows both **Passed** and **Applicable**. The difference between `critic_clean_before`
and `critic_clean_after` is what the reflection loop bought. Also reported: mean rounds used, and
mean critic errors and warnings for the first and the kept draft.

A sample of the Markdown summary (offline run on this repository's own history):

```text
| Metric | Passed | Applicable | Rate |
|---|---:|---:|---:|
| Convention matches the real commit | 10 | 10 | 100.0% |
| Subject length within the repository's profile | 0 | 10 | 0.0% |
| Imperative mood | 10 | 10 | 100.0% |
| Scope presence agrees with the real commit | 7 | 10 | 70.0% |
| Critic finds no errors in the first draft | 0 | 10 | 0.0% |
| Critic finds no errors in the final draft | 10 | 10 | 100.0% |
```

The stand-in writes short messages, so it fails the length check against a repository whose real
subjects are long. That is the check working, and not a statement about any model.

### JSON schema

`report.json` is a serialization of `scripts.eval.report.Report` (`schema_version` is `1`):

```text
schema_version, generated_at, repository, mode ("offline" | "live"), provider, model,
requested, max_iterations,
commits[]:  hash, real_subject, error,
            outcome: generated_message, real_convention, generated_convention,
                     iterations, approved, interrupted,
                     checks {convention_match, subject_length_ok, imperative_mood,
                             capitalization_agrees, punctuation_agrees, scope_agrees, body_agrees},
                     before {errors, warnings, rules[]}, after {errors, warnings, rules[]}
aggregate:  evaluated, failed, interrupted,
            metrics{<key>: {passed, applicable, rate}},
            mean_iterations, mean_errors_before, mean_errors_after,
            mean_warnings_before, mean_warnings_after
```

`error` is set (and `outcome` is `null`) for a commit that could not be evaluated. A check that
does not apply is `null`; a rate with nothing applicable is `null`.

## Caveats

- **The real message is a reference, not the truth.** A generated message can be good and still
  disagree with a hurried real commit. Compare runs against each other, and read the per-commit
  table, before reading much into one number.
- **History is scoped to what came before.** Early commits in a repository have little or no
  history, so their style profile is thin, exactly as it would have been for a real user.
- **Merge commits are skipped.** Rebased or squashed history is replayed as it appears.
- **A live run costs money and varies.** Models are not deterministic; run the same repository a
  few times before comparing two models.

## Tests

`tests/eval/` covers the harness without a network: replay on temporary repositories (parent
diffs, root commits, history scoping, merges, renames, an untouched working tree), the offline
stand-in through the real graph, each metric's boundaries, the aggregate arithmetic, the report
schema, and the command line including its failure paths.
