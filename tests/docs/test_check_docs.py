"""The documentation checker: it passes on the real docs, and it catches each kind of mistake."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts import check_docs
from scripts.check_docs import (
    ROOT,
    Problem,
    check_all,
    check_document,
    collect_cli_surface,
    known_environment_variables,
)

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.check_docs import CliSurface


@pytest.fixture(scope="module")
def surface() -> CliSurface:
    return collect_cli_surface()


def _check(tmp_path: Path, surface: CliSurface, text: str, name: str = "README.md") -> list[str]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return [problem.message for problem in check_document(path, tmp_path, surface)]


def _mentions(problems: list[str], *needles: str) -> bool:
    return any(all(n in problem for n in needles) for problem in problems)


class TestTheRealDocuments:
    def test_every_document_agrees_with_the_code(self) -> None:
        assert [str(problem) for problem in check_all(ROOT)] == []

    def test_the_command_line_reports_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert check_docs.main() == 0
        assert "no problems" in capsys.readouterr().out

    def test_the_command_line_fails_and_lists_the_problems(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(check_docs, "check_all", lambda: [Problem("README.md", "it is wrong")])
        assert check_docs.main() == 1
        assert "README.md: it is wrong" in capsys.readouterr().err

    def test_the_surface_comes_from_the_real_apps(self, surface: CliSurface) -> None:
        assert {"--dry-run", "--accept-partial", "--hint", "--amend", "-m"} <= surface.flags
        assert "hook install" in surface.commands["gitcommit-ai"]
        assert "hook install" in surface.commands["git-ai-commit"]
        assert "generate" not in surface.commands["git-ai-commit"]
        assert "generate" in surface.commands["gitcommit-ai"]

    def test_every_environment_variable_the_code_reads_is_known(self) -> None:
        known = known_environment_variables()
        assert {"GITCOMMIT_AI_PROVIDER", "GITCOMMIT_AI_ENV_FILE", "GITCOMMIT_AI_HOOK"} <= known
        assert {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"} <= known
        assert "GITCOMMIT_AI_CONFIG_DIR" in known


class TestFlagsAndVariables:
    def test_an_unknown_flag_is_reported(self, tmp_path: Path, surface: CliSurface) -> None:
        problems = _check(tmp_path, surface, "Run `gitcommit-ai generate --dry-runn`.\n")
        assert _mentions(problems, "--dry-runn")

    def test_flags_that_exist_or_belong_to_other_tools_pass(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = "`--dry-run`, `--accept-partial`, `pipx install --force` and `-m`.\n"
        assert _check(tmp_path, surface, text) == []

    def test_an_unknown_short_flag_is_reported(self, tmp_path: Path, surface: CliSurface) -> None:
        assert _mentions(_check(tmp_path, surface, "Use `-z` here.\n"), "-z")

    def test_flags_inside_code_blocks_are_checked(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = "```bash\ngit ai-commit --bogus\n```\n"
        assert _mentions(_check(tmp_path, surface, text), "--bogus")

    @pytest.mark.parametrize("variable", ["GITCOMMIT_AI_BOGUS", "ACME_API_KEY"])
    def test_an_unknown_variable_is_reported(
        self, tmp_path: Path, surface: CliSurface, variable: str
    ) -> None:
        assert _mentions(_check(tmp_path, surface, f"Set `{variable}`.\n"), variable)

    def test_known_variables_pass_in_any_document(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = "# GITCOMMIT_AI_PROVIDER=ollama\n# OPENAI_API_KEY=\n"
        assert _check(tmp_path, surface, text, name=".env.example") == []
        assert _mentions(_check(tmp_path, surface, "# NOPE_API_KEY=\n", ".env.example"), "NOPE")


class TestCommands:
    @pytest.mark.parametrize(
        "text",
        [
            "`gitcommit-ai frobnicate`",
            "`git-ai-commit generate`",
            "```bash\ngit-ai-commit hook explode\n```",
        ],
    )
    def test_commands_that_do_not_exist_are_reported(
        self, tmp_path: Path, surface: CliSurface, text: str
    ) -> None:
        assert _mentions(_check(tmp_path, surface, text + "\n"), "not a command")

    @pytest.mark.parametrize(
        "text",
        [
            "`gitcommit-ai generate --dry-run`",
            "`git-ai-commit hook install`",
            "`git-ai-commit config`",
            "`git ai-commit --dry-run`",
            "`git ai-commit hook uninstall`",
            "```bash\ncd gitcommit-ai\npipx install .\n```",
            "`gitcommit-ai context --count 5`",
        ],
    )
    def test_real_commands_and_plain_text_pass(
        self, tmp_path: Path, surface: CliSurface, text: str
    ) -> None:
        assert _check(tmp_path, surface, text + "\n") == []

    def test_a_make_target_must_exist(self, tmp_path: Path, surface: CliSurface) -> None:
        (tmp_path / "Makefile").write_text("demo:\n\techo hi\n", encoding="utf-8")
        assert _check(tmp_path, surface, "Run `make demo`.\n") == []
        assert _mentions(_check(tmp_path, surface, "Run `make deploy`.\n"), "make deploy")

    def test_a_python_module_must_be_runnable(self, tmp_path: Path, surface: CliSurface) -> None:
        assert _check(tmp_path, surface, "`python -m scripts.eval REPO`\n") == []
        assert _check(tmp_path, surface, "`python -m scripts.check_docs`\n") == []
        assert _mentions(_check(tmp_path, surface, "`python -m scripts.metrics`\n"), "metrics")
        assert _mentions(_check(tmp_path, surface, "`python -m scripts.nope`\n"), "scripts.nope")


class TestDefaults:
    def test_a_wrong_default_is_reported(self, tmp_path: Path, surface: CliSurface) -> None:
        row = "| `GITCOMMIT_AI_MAX_RETRIES` | `9` | Retries |\n"
        assert _mentions(_check(tmp_path, surface, row), "GITCOMMIT_AI_MAX_RETRIES", "9")

    @pytest.mark.parametrize(
        "row",
        [
            "| `GITCOMMIT_AI_MAX_RETRIES` | `3` | Retries |",
            "| `GITCOMMIT_AI_TIMEOUT` | `60` | Seconds |",
            "| `GITCOMMIT_AI_PROVIDER` | `openai` | Provider |",
            "| `GITCOMMIT_AI_MODEL` | provider default | Model |",
            "| `GITCOMMIT_AI_ENV_FILE` | none | A file |",
        ],
    )
    def test_correct_defaults_pass(self, tmp_path: Path, surface: CliSurface, row: str) -> None:
        assert _check(tmp_path, surface, row + "\n") == []

    def test_a_wrong_provider_default_model_is_reported(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        row = "| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-9` |\n"
        assert _mentions(_check(tmp_path, surface, row), "OpenAI", "gpt-9")

    def test_a_wrong_provider_key_variable_is_reported(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        row = "| Gemini | `gemini` | `GEMINI_KEY` | `gemini-2.5-flash` |\n"
        assert _mentions(_check(tmp_path, surface, row), "Gemini", "GEMINI_KEY")

    def test_correct_provider_rows_pass(self, tmp_path: Path, surface: CliSurface) -> None:
        rows = (
            "| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |\n"
            "| Ollama | `ollama` | none (local server) | `llama3.1` |\n"
        )
        assert _check(tmp_path, surface, rows) == []


class TestPathsAndLinks:
    def test_a_missing_path_is_reported(self, tmp_path: Path, surface: CliSurface) -> None:
        assert _mentions(_check(tmp_path, surface, "See `src/nothing.py`.\n"), "src/nothing.py")

    def test_existing_paths_and_tails_of_paths_pass(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        (tmp_path / "pkg" / "agents").mkdir(parents=True)
        (tmp_path / "pkg" / "agents" / "state.py").write_text("", encoding="utf-8")
        (tmp_path / "LICENSE").write_text("", encoding="utf-8")
        assert (
            _check(tmp_path, surface, "`pkg/agents/state.py`, `agents/state.py`, `pkg/`\n") == []
        )

    def test_generated_and_non_path_text_is_ignored(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = (
            "`docs/assets/demo.gif`, `/tmp/x`, `+/-`, `src/<x>`, `a b/c.py`, `~/f`, "
            "`config.toml`, `credentials.toml`\n"
        )
        assert _check(tmp_path, surface, text) == []

    def test_a_broken_relative_link_is_reported(self, tmp_path: Path, surface: CliSurface) -> None:
        assert _mentions(_check(tmp_path, surface, "[x](docs/none.md)\n"), "docs/none.md")

    def test_a_working_relative_link_and_external_links_pass(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        (tmp_path / "CONTRIBUTING.md").write_text("# Hi\n", encoding="utf-8")
        text = "[a](CONTRIBUTING.md) [b](https://example.com/x) [c](mailto:a@b.c)\n"
        assert _check(tmp_path, surface, text) == []

    def test_anchors_are_checked_against_github_slugs(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = "# Why it's different\n\n[ok](#why-its-different) [bad](#nowhere)\n"
        problems = _check(tmp_path, surface, text)
        assert _mentions(problems, "#nowhere")
        assert not _mentions(problems, "why-its-different")

    def test_anchors_in_another_file_are_checked(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        (tmp_path / "other.md").write_text("## Real heading\n", encoding="utf-8")
        assert _check(tmp_path, surface, "[x](other.md#real-heading)\n") == []
        assert _mentions(_check(tmp_path, surface, "[x](other.md#fake)\n"), "#fake")

    def test_images_and_html_sources_must_exist(self, tmp_path: Path, surface: CliSurface) -> None:
        text = '![logo](docs/assets/logo.svg)\n<img src="docs/assets/other.svg">\n'
        problems = _check(tmp_path, surface, text)
        assert _mentions(problems, "docs/assets/logo.svg")
        assert _mentions(problems, "docs/assets/other.svg")
        (tmp_path / "docs" / "assets").mkdir(parents=True)
        (tmp_path / "docs" / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        (tmp_path / "docs" / "assets" / "other.svg").write_text("<svg/>", encoding="utf-8")
        assert _check(tmp_path, surface, text) == []

    def test_srcset_in_a_picture_element_is_checked(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        text = '<source media="(prefers-color-scheme: dark)" srcset="dark.svg">\n'
        assert _mentions(_check(tmp_path, surface, text), "dark.svg")


class TestDemoGif:
    GIF = "![demo](docs/assets/demo.gif)\n"

    def _record(self, root: Path) -> None:
        (root / "docs" / "assets").mkdir(parents=True)
        (root / "docs" / "assets" / "demo.gif").write_bytes(b"GIF89a")

    def test_embedding_an_unrecorded_gif_is_reported(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        assert _mentions(_check(tmp_path, surface, self.GIF), "has not been recorded")

    def test_a_recorded_gif_must_be_shown(self, tmp_path: Path, surface: CliSurface) -> None:
        self._record(tmp_path)
        assert _mentions(_check(tmp_path, surface, "No demo here.\n"), "does not embed it")

    def test_a_recorded_and_embedded_gif_passes(self, tmp_path: Path, surface: CliSurface) -> None:
        self._record(tmp_path)
        assert _check(tmp_path, surface, self.GIF) == []

    def test_mentioning_the_path_in_code_without_embedding_is_fine(
        self, tmp_path: Path, surface: CliSurface
    ) -> None:
        assert _check(tmp_path, surface, "It writes `docs/assets/demo.gif`.\n") == []


class TestMermaid:
    @staticmethod
    def _block(body: str) -> str:
        return f"```mermaid\n{body}\n```\n"

    def test_a_valid_flowchart_passes(self, tmp_path: Path, surface: CliSurface) -> None:
        body = 'flowchart TD\n    A["One (1)"] -->|"yes"| B{"Two?"}\n    B --> C["Three"]'
        assert _check(tmp_path, surface, self._block(body)) == []

    @pytest.mark.parametrize(
        ("body", "needle"),
        [
            ('flowchart TD\n    A["unclosed] --> B', "unclosed quote"),
            ('flowchart TD\n    A["x" --> B["y"]', "unbalanced []"),
            ('flowchart TD\n    A{"x"} --> B(("y")', "unbalanced ()"),
            ("flowchart TD\n    A -->", "missing end"),
            ("flowchart TD\n    A -->|oops B", "unbalanced edge label"),
            ("pie\n    A --> B", "not a diagram type"),
        ],
    )
    def test_broken_diagrams_are_reported(
        self, tmp_path: Path, surface: CliSurface, body: str, needle: str
    ) -> None:
        assert _mentions(_check(tmp_path, surface, self._block(body)), needle)

    def test_a_sequence_diagram_passes(self, tmp_path: Path, surface: CliSurface) -> None:
        body = "sequenceDiagram\n    participant A\n    A->>A: confirm (unless --yes)"
        assert _check(tmp_path, surface, self._block(body)) == []


class TestCompleteness:
    def _readme(self, root: Path, edges: str, extra: str = "") -> None:
        variables = " ".join(sorted(known_environment_variables()))
        flags = " ".join(sorted(f for f in collect_cli_surface().flags if f.startswith("--")))
        diagram = (
            "```mermaid\nflowchart LR\n"
            '    cli["cli"]\n    core["core"]\n    agents["agents"]\n' + edges + "\n```\n"
        )
        (root / "README.md").write_text(
            f"{variables}\n{flags}\n{diagram}{extra}", encoding="utf-8"
        )
        (root / "docs").mkdir(exist_ok=True)

    REAL = (
        "    cli --> agents\n    cli --> git\n    agents --> context\n    agents --> llm\n"
        "    context --> core\n    git --> core\n    llm --> core"
    )

    def test_the_real_layering_passes(self, tmp_path: Path) -> None:
        self._readme(tmp_path, self.REAL)
        problems = [
            p for p in check_docs._check_completeness(tmp_path) if p.document == "README.md"
        ]
        assert problems == []

    def test_an_invented_edge_is_reported(self, tmp_path: Path) -> None:
        self._readme(tmp_path, self.REAL + "\n    core --> cli")
        messages = [p.message for p in check_docs._check_completeness(tmp_path)]
        assert _mentions(messages, "core -> cli", "no import has")

    def test_an_omitted_edge_is_reported(self, tmp_path: Path) -> None:
        self._readme(tmp_path, self.REAL.replace("    git --> core\n", ""))
        messages = [p.message for p in check_docs._check_completeness(tmp_path)]
        assert _mentions(messages, "git -> core", "the code has")

    def test_an_undocumented_variable_is_reported(self, tmp_path: Path) -> None:
        self._readme(tmp_path, self.REAL)
        text = (
            (tmp_path / "README.md").read_text(encoding="utf-8").replace("GITCOMMIT_AI_HOOK", "")
        )
        (tmp_path / "README.md").write_text(text, encoding="utf-8")
        messages = [p.message for p in check_docs._check_completeness(tmp_path)]
        assert _mentions(messages, "does not document GITCOMMIT_AI_HOOK")

    def test_an_undocumented_generate_flag_is_reported(self, tmp_path: Path) -> None:
        self._readme(tmp_path, self.REAL)
        text = (tmp_path / "README.md").read_text(encoding="utf-8").replace("--accept-partial", "")
        (tmp_path / "README.md").write_text(text, encoding="utf-8")
        messages = [p.message for p in check_docs._check_completeness(tmp_path)]
        assert _mentions(messages, "--accept-partial")

    def test_a_missing_layer_diagram_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("no diagram\n", encoding="utf-8")
        messages = [p.message for p in check_docs._check_completeness(tmp_path)]
        assert _mentions(messages, "has no layer diagram")

    def test_a_missing_document_is_reported(self, tmp_path: Path) -> None:
        assert any(p.message == "the document does not exist" for p in check_all(tmp_path))
