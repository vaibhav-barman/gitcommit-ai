"""Check that the documentation says what the code does.

Run from the repository root:

    uv run python -m scripts.check_docs

Everything a document names is looked up in the live code: commands and flags (from
the real Typer apps), environment variables, provider defaults, file paths, links,
heading anchors, the layer diagram (against the real import graph), Mermaid syntax,
and the demo GIF rule. Exit code 1 if anything is wrong.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import typer.main
from typer.core import TyperGroup, TyperOption

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.main import app as main_app
from git_commit_ai.core.config import ENV_FIELDS, GitCommitAIConfig, ProviderName
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.git.hooks import SKIP_VARIABLE
from git_commit_ai.llm.factory import api_key_variable, default_model
from scripts.eval.cli import app as eval_app

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    "README.md",
    "docs/architecture.md",
    "scripts/eval/README.md",
    "CONTRIBUTING.md",
    ".env.example",
)
DEMO_GIF = "docs/assets/demo.gif"

# Flags that belong to other tools, with the tool that owns them. A flag named in the docs
# must be ours or be listed here, so a typo in one of ours cannot hide behind this list.
EXTERNAL_FLAGS: dict[str, str] = {
    "--force": "pipx install",
    "--all-extras": "uv sync / uv run",
    "--extra": "uv sync",
    "--quiet": "uv run",
    "--project": "uv run",
    "--allow-empty": "git commit",
    "--no-merges": "git log",
    "--cached": "git diff",
    "--name-only": "git diff",
    "--name-status": "git diff",
    "--numstat": "git diff",
    "--no-color": "git diff",
    "--format": "git log",
    "--short": "git symbolic-ref",
    "--others": "git ls-files",
    "--exclude-standard": "git ls-files",
    "--diff-filter": "git diff",
    "--git-path": "git rev-parse",
    "--show-toplevel": "git rev-parse",
    "--amend": "git commit (also ours)",
    "--no-verify": "git commit (also ours)",
    "--check": "ruff format",
    "--python": "uv run",
    "--help": "every tool",
    "--version": "every tool",
}
EXTERNAL_SHORT_FLAGS = {"-m", "-F", "-c", "-C", "-p", "-h", "-n", "-o", "-v", "-y", "-u", "-M"}
# Paths a document may name that are produced by running something, not committed.
GENERATED_PATHS = {DEMO_GIF, "eval-output", "report.json", "report.md", ".env"}
_PATH_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".toml", ".tape", ".sh", ".svg", ".gif", ".json")
_NOT_REPO_PATH_PREFIXES = ("/", "~", "$", "http", "git@", "<", ".git/")
_SRC = ROOT / "src" / "git_commit_ai"

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.DOTALL | re.MULTILINE)
_CODE_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
_HTML_URL = re.compile(r"""(?:src|srcset|href)="([^"]+)\"""")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.MULTILINE)
_LONG_FLAG = re.compile(r"(?<![\w/-])(--[a-z][a-z0-9-]*)")
_SHORT_FLAG_SPAN = re.compile(r"^-[A-Za-z]$")
_ENV_NAME = re.compile(r"\b(GITCOMMIT_AI_[A-Z_]+|[A-Z][A-Z0-9]*_API_KEY)\b")
_COMMAND = re.compile(
    r"(?<![\w-])(gitcommit-ai|git-ai-commit|git ai-commit)"
    r"((?:[ \t]+[a-z][a-z-]*)+)(?=[ \t]|$|[`)])"
)
_MAKE = re.compile(r"(?<![\w-])make\s+([a-z][a-z-]*)")
_MODULE = re.compile(r"python -m ([\w.]+)")
_DEFAULT_ROW = re.compile(r"^\|\s*`(GITCOMMIT_AI_[A-Z_]+)`\s*\|\s*`?([^|`]+?)`?\s*\|")
_PROVIDER_ROW = re.compile(r"^\|\s*(OpenAI|Anthropic|Gemini|Ollama)\s*\|(.*)\|\s*$")


@dataclass(frozen=True)
class Problem:
    """One thing a document says that the code does not back up."""

    document: str
    message: str

    def __str__(self) -> str:
        return f"{self.document}: {self.message}"


@dataclass(frozen=True)
class CliSurface:
    """Commands and flags of the real command-line apps."""

    commands: dict[str, set[str]]
    flags: set[str]


def collect_cli_surface() -> CliSurface:
    """Introspect the real Typer apps, so the docs are checked against the code that runs."""
    flags: set[str] = {"--help", "-h"}
    commands: dict[str, set[str]] = {}

    def walk(command: object, path: tuple[str, ...], entry: str) -> None:
        for param in getattr(command, "params", []):
            if isinstance(param, TyperOption):
                flags.update(
                    opt for opt in (*param.opts, *param.secondary_opts) if opt.startswith("-")
                )
        if isinstance(command, TyperGroup):
            for name, sub in command.commands.items():
                commands.setdefault(entry, set()).add(" ".join((*path, name)))
                walk(sub, (*path, name), entry)

    for entry, typer_app in (
        ("gitcommit-ai", main_app),
        ("git-ai-commit", git_app),
        ("git ai-commit", git_app),
        ("python -m scripts.eval", eval_app),
    ):
        walk(typer.main.get_command(typer_app), (), entry)
        commands.setdefault(entry, set())
    flags.update({"--install-completion", "--show-completion"})
    return CliSurface(commands=commands, flags=flags)


def known_environment_variables() -> set[str]:
    """Every environment variable the code reads."""
    keys = {api_key_variable(provider) for provider in ProviderName}
    return set(ENV_FIELDS) | {ENV_FILE_VARIABLE, SKIP_VARIABLE} | {k for k in keys if k}


def import_edges(package: Path = _SRC) -> set[tuple[str, str]]:
    """Which top-level subpackage imports which, read from the source."""
    edges: set[tuple[str, str]] = set()
    for path in package.rglob("*.py"):
        layer = path.relative_to(package).parts[0]
        if layer.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "git_commit_ai."
            ):
                target = (node.module or "").split(".")[1]
                if target != layer:
                    edges.add((layer, target))
    return edges


def real_layer_edges(package: Path = _SRC) -> set[tuple[str, str]]:
    """The import graph as a layer diagram draws it.

    Implied edges are dropped (``cli -> core`` when ``cli -> agents -> context -> core``
    already holds).
    """
    edges = import_edges(package)
    return {edge for edge in edges if not _implied(edge, edges)}


def _implied(edge: tuple[str, str], edges: set[tuple[str, str]]) -> bool:
    """True if ``edge`` is already covered by a longer path through other layers."""
    source, target = edge
    stack = [b for a, b in edges if a == source and (a, b) != edge]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(b for a, b in edges if a == node)
    return False


def check_all(root: Path = ROOT) -> list[Problem]:
    """Check every documented file under ``root``."""
    surface = collect_cli_surface()
    problems: list[Problem] = []
    for name in DOCUMENTS:
        path = root / name
        if not path.exists():
            problems.append(Problem(name, "the document does not exist"))
            continue
        problems.extend(check_document(path, root, surface))
    problems.extend(_check_completeness(root))
    return problems


def check_document(path: Path, root: Path, surface: CliSurface | None = None) -> list[Problem]:
    """Check one document against the code."""
    surface = surface or collect_cli_surface()
    name = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    problems: list[Problem] = []

    if path.suffix == ".md":
        problems += _check_flags(name, text, surface)
        problems += _check_commands(name, _code_text(text), surface, root)
        problems += _check_paths(name, text, root)
        problems += _check_links(name, text, path)
        problems += _check_demo_gif(name, text, root)
        problems += _check_mermaid(name, text)
        problems += _check_defaults(name, text)
    problems += _check_environment(name, text)
    return problems


def _code_text(text: str) -> str:
    """Fenced blocks and inline code spans, where commands and flags are meant literally."""
    fenced = [m.group(2) for m in _FENCE.finditer(text) if m.group(1) != "mermaid"]
    prose = _FENCE.sub("", text)
    return "\n".join([*fenced, *_CODE_SPAN.findall(prose)])


def _check_flags(name: str, text: str, surface: CliSurface) -> list[Problem]:
    problems: list[Problem] = []
    known = surface.flags | set(EXTERNAL_FLAGS)
    for flag in sorted(set(_LONG_FLAG.findall(text))):
        if flag not in known:
            problems.append(Problem(name, f"names the flag {flag}, which no command defines"))
    for span in _CODE_SPAN.findall(_FENCE.sub("", text)):
        for part in (p.strip() for p in span.split(",")):
            if _SHORT_FLAG_SPAN.match(part) and part not in surface.flags | EXTERNAL_SHORT_FLAGS:
                problems.append(
                    Problem(name, f"names the short flag {part}, which nothing defines")
                )
    return problems


def _check_commands(name: str, code: str, surface: CliSurface, root: Path) -> list[Problem]:
    problems: list[Problem] = []
    for match in _COMMAND.finditer(code):
        entry, rest = match.group(1), match.group(2).split()
        available = surface.commands.get(entry, set())
        # Take the longest leading run of words that is a real command; the rest are arguments.
        found = [
            " ".join(rest[: n + 1])
            for n in range(len(rest))
            if " ".join(rest[: n + 1]) in available
        ]
        if not found:
            problems.append(Problem(name, f"runs '{entry} {rest[0]}', which is not a command"))
            continue
        best = found[-1]
        is_group = any(other.startswith(f"{best} ") for other in available)
        if is_group and len(rest) > len(best.split()):
            unknown = f"{best} {rest[len(best.split())]}"
            problems.append(Problem(name, f"runs '{entry} {unknown}', which is not a command"))
    for target in _MAKE.findall(code):
        makefile = root / "Makefile"
        if not makefile.exists() or f"\n{target}:" not in "\n" + makefile.read_text(
            encoding="utf-8"
        ):
            problems.append(Problem(name, f"runs 'make {target}', which the Makefile lacks"))
    for module in _MODULE.findall(code):
        if not _is_runnable(module):
            problems.append(Problem(name, f"runs 'python -m {module}', which is not runnable"))
    return problems


def _is_runnable(module: str) -> bool:
    """``python -m module`` works for a package with ``__main__.py`` or a module that
    guards a ``__main__`` entry point."""
    try:
        spec = importlib.util.find_spec(module)
        if spec is None:
            return False
        if spec.submodule_search_locations is not None:
            return importlib.util.find_spec(f"{module}.__main__") is not None
    except ImportError:
        return False
    origin = spec.origin
    return origin is not None and '__name__ == "__main__"' in Path(origin).read_text(
        encoding="utf-8"
    )


def _check_environment(name: str, text: str) -> list[Problem]:
    known = known_environment_variables()
    return [
        Problem(name, f"names the variable {variable}, which the code does not read")
        for variable in sorted(set(_ENV_NAME.findall(text)) - known)
    ]


def _check_defaults(name: str, text: str) -> list[Problem]:
    problems: list[Problem] = []
    defaults = GitCommitAIConfig().model_dump()
    for line in text.splitlines():
        row = _DEFAULT_ROW.match(line)
        if row and (field := ENV_FIELDS.get(row.group(1))):
            documented, actual = row.group(2).strip(), defaults[field]
            if actual is not None and not _same_default(documented, actual):
                problems.append(
                    Problem(
                        name,
                        f"documents {row.group(1)} default as {documented}, code has {actual}",
                    )
                )
        provider = _PROVIDER_ROW.match(line)
        if provider:
            problems += _check_provider_row(name, provider.group(1), provider.group(2))
    return problems


def _same_default(documented: str, actual: object) -> bool:
    if isinstance(actual, bool):
        return documented.lower() == str(actual).lower()
    if isinstance(actual, int | float):
        try:
            return float(documented) == float(actual)
        except ValueError:
            return False
    return documented == str(getattr(actual, "value", actual))


def _check_provider_row(name: str, label: str, cells: str) -> list[Problem]:
    provider = ProviderName(label.lower())
    columns = [c.strip().strip("`") for c in cells.split("|")]
    key = api_key_variable(provider)
    problems: list[Problem] = []
    if columns[0] != provider.value:
        problems.append(Problem(name, f"lists the {label} extra as {columns[0]}"))
    if key and columns[1] != key:
        problems.append(
            Problem(name, f"lists the {label} key variable as {columns[1]}, code has {key}")
        )
    if key is None and not columns[1].startswith("none"):
        problems.append(Problem(name, f"lists a key for {label}, which needs none"))
    if columns[2] != default_model(provider):
        model = default_model(provider)
        problems.append(
            Problem(name, f"lists the {label} model as {columns[2]}, code has {model}")
        )
    return problems


def _check_paths(name: str, text: str, root: Path) -> list[Problem]:
    problems: list[Problem] = []
    files = _repository_files(root)
    for span in sorted(set(_CODE_SPAN.findall(_FENCE.sub("", text)))):
        token = span.strip()
        if " " in token or not _looks_like_repo_path(token):
            continue
        relative = token.rstrip("/")
        if relative in GENERATED_PATHS or _exists(relative, root, files):
            continue
        problems.append(Problem(name, f"names the path {token}, which does not exist"))
    return problems


def _repository_files(root: Path) -> set[str]:
    """Every file and directory under ``root`` (relative, POSIX), skipping tool folders."""
    skipped = {".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
    found: set[str] = set()
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if not skipped.intersection(parts):
            found.add("/".join(parts))
    return found


def _exists(relative: str, root: Path, files: set[str]) -> bool:
    """A path from the repository root, or a shorter one inside it (``agents/state.py``,
    ``retry.py``) that is the tail of a real path."""
    if (root / relative).exists():
        return True
    return any(f == relative or f.endswith(f"/{relative}") for f in files)


def _looks_like_repo_path(token: str) -> bool:
    if token.startswith(_NOT_REPO_PATH_PREFIXES) or not re.fullmatch(r"[\w./-]+", token):
        return False
    return "/" in token or token.endswith(_PATH_SUFFIXES)


def _check_links(name: str, text: str, path: Path) -> list[Problem]:
    problems: list[Problem] = []
    targets = [*_LINK.findall(_FENCE.sub("", text)), *_HTML_URL.findall(text)]
    anchors = {path: _anchors(text)}
    for target in sorted(set(targets)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        file_part, _, fragment = target.partition("#")
        destination = path if not file_part else (path.parent / file_part).resolve()
        if file_part and not destination.exists():
            problems.append(Problem(name, f"links to {file_part}, which does not exist"))
            continue
        if fragment and destination.suffix == ".md":
            if destination not in anchors:
                anchors[destination] = _anchors(destination.read_text(encoding="utf-8"))
            if fragment not in anchors[destination]:
                problems.append(
                    Problem(name, f"links to the missing anchor #{fragment} in {destination.name}")
                )
    return problems


def _anchors(text: str) -> set[str]:
    """GitHub's heading slugs: lowercase, punctuation dropped, spaces to hyphens."""
    return {
        re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.lower().replace("`", "")))
        for heading in _HEADING.findall(_FENCE.sub("", text))
    }


def _check_demo_gif(name: str, text: str, root: Path) -> list[Problem]:
    """An embedded demo must exist, and a recorded demo must be shown."""
    embedded = bool(
        re.search(rf"!\[[^\]]*\]\([^)]*{re.escape(DEMO_GIF)}\)|src=\"[^\"]*demo\.gif\"", text)
    )
    exists = (root / DEMO_GIF).exists()
    if name != "README.md":
        return []
    if embedded and not exists:
        return [Problem(name, f"embeds {DEMO_GIF}, which has not been recorded")]
    if exists and not embedded:
        return [Problem(name, f"{DEMO_GIF} exists but the README does not embed it")]
    return []


def _check_mermaid(name: str, text: str) -> list[Problem]:
    problems: list[Problem] = []
    for index, match in enumerate(_FENCE.finditer(text), start=1):
        if match.group(1) != "mermaid":
            continue
        label = f"mermaid block {index}"
        body = match.group(2)
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        if not re.match(
            r"^(flowchart (TD|LR|TB|RL|BT)|graph (TD|LR|TB|RL|BT)|sequenceDiagram)\b", first
        ):
            problems.append(Problem(name, f"{label} starts with '{first}', not a diagram type"))
            continue
        if body.count('"') % 2:
            problems.append(Problem(name, f"{label} has an unclosed quote"))
        unquoted = re.sub(r'"[^"]*"', "", body)
        for opening, closing in ("[]", "()", "{}"):
            if unquoted.count(opening) != unquoted.count(closing):
                problems.append(Problem(name, f"{label} has unbalanced {opening}{closing}"))
        if first.startswith(("flowchart", "graph")):
            problems += _check_flowchart_edges(name, label, body)
    return problems


def _check_flowchart_edges(name: str, label: str, body: str) -> list[Problem]:
    problems: list[Problem] = []
    for line in body.splitlines():
        stripped = line.strip()
        if "-->" not in stripped:
            continue
        parts = re.split(r"\s*-->(?:\|[^|]*\|)?\s*", stripped)
        if len(parts) < 2 or any(not part for part in parts):
            problems.append(Problem(name, f"{label} has an edge with a missing end: {stripped}"))
        if stripped.count("|") % 2:
            problems.append(Problem(name, f"{label} has an unbalanced edge label: {stripped}"))
    return problems


def _diagram_edges(text: str) -> set[tuple[str, str]]:
    """Edges of a layer diagram: ``cli["cli"] --> agents["agents"]`` gives (cli, agents)."""
    edges: set[tuple[str, str]] = set()
    for match in re.finditer(r"^\s*(\w+)(?:\[[^\]]*\])?\s*-->\s*(\w+)", text, re.MULTILINE):
        edges.add((match.group(1), match.group(2)))
    return edges


def _check_completeness(root: Path) -> list[Problem]:
    """The README is the configuration reference, so it must mention every variable, and the
    layer diagram in both documents must equal the real import graph."""
    problems: list[Problem] = []
    readme = root / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        for variable in sorted(known_environment_variables()):
            if variable not in text:
                problems.append(Problem("README.md", f"does not document {variable}"))
        for flag in sorted(_generate_flags()):
            if flag not in text:
                problems.append(
                    Problem("README.md", f"does not document the generate flag {flag}")
                )
    real = real_layer_edges()
    for name in ("README.md", "docs/architecture.md"):
        path = root / name
        if not path.exists():
            continue
        blocks = [
            m.group(2)
            for m in _FENCE.finditer(path.read_text(encoding="utf-8"))
            if m.group(1) == "mermaid"
        ]
        layer_blocks = [b for b in blocks if "cli[" in b and "core[" in b]
        if not layer_blocks:
            problems.append(Problem(name, "has no layer diagram"))
            continue
        drawn = _diagram_edges(layer_blocks[0])
        for edge in sorted(drawn - real):
            problems.append(
                Problem(name, f"layer diagram shows {edge[0]} -> {edge[1]}, which no import has")
            )
        for edge in sorted(real - drawn):
            problems.append(
                Problem(name, f"layer diagram omits {edge[0]} -> {edge[1]}, which the code has")
            )
    return problems


def _generate_flags() -> set[str]:
    command = typer.main.get_command(main_app)
    assert isinstance(command, TyperGroup)
    generate = command.commands["generate"]
    return {
        opt
        for param in generate.params
        if isinstance(param, TyperOption)
        for opt in param.opts
        if opt.startswith("--")
    } - {"--help"}


def main() -> int:
    """Print every problem and return the exit code."""
    problems = check_all()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} documentation problem(s).", file=sys.stderr)
        return 1
    print(f"Checked {len(DOCUMENTS)} documents against the code: no problems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
