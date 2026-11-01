"""The dependency direction the architecture promises, checked against the real imports."""

from __future__ import annotations

from scripts.check_docs import import_edges, real_layer_edges

# Each layer and the layers it may import. Anything else is a violation.
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "git": {"core"},
    "context": {"core"},
    "llm": {"core"},
    "agents": {"context", "core", "llm"},
    "onboarding": {"core", "llm"},
    "cli": {"agents", "context", "core", "git", "llm", "onboarding"},
}


def test_every_layer_imports_only_what_it_may() -> None:
    violations = sorted(
        f"{source} imports {target}"
        for source, target in import_edges()
        if target not in ALLOWED.get(source, set())
    )
    assert violations == []


def test_agents_never_touch_git_and_nothing_imports_the_cli() -> None:
    edges = import_edges()
    assert ("agents", "git") not in edges
    assert not {("onboarding", "agents"), ("onboarding", "context"), ("onboarding", "git")} & edges
    assert not any(target == "cli" for _, target in edges)


def test_every_package_is_a_known_layer() -> None:
    layers = {source for source, _ in import_edges()} | {target for _, target in import_edges()}
    assert layers <= set(ALLOWED)


def test_the_drawn_layers_are_the_real_ones_without_implied_edges() -> None:
    assert real_layer_edges() == {
        ("cli", "agents"),
        ("cli", "git"),
        ("agents", "context"),
        ("agents", "llm"),
        ("context", "core"),
        ("git", "core"),
        ("llm", "core"),
        ("onboarding", "llm"),
    }
