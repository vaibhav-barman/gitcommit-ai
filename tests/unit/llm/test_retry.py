"""Unit tests for bounded exponential backoff."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import (
    LLMError,
    LLMResponseError,
    MissingAPIKeyError,
    OllamaUnreachableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm import FakeLLM, LLMMessage, Role, create_provider
from git_commit_ai.llm.retry import RetryEvent, RetryingProvider, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence

PROMPT = [LLMMessage(role=Role.USER, content="go")]
POLICY = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=8.0)


class Flaky:
    """Fails with the queued errors, then succeeds."""

    name = "flaky"
    model = "flaky-1"

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "ok"


def _retrying(inner: Flaky, policy: RetryPolicy = POLICY) -> tuple[RetryingProvider, list[float]]:
    waits: list[float] = []
    return RetryingProvider(inner, policy, sleep=waits.append), waits


class TestPolicy:
    def test_delay_doubles_and_is_capped(self) -> None:
        error = TransientLLMError("p", "x")
        policy = RetryPolicy(max_retries=6, base_delay=1.0, max_delay=5.0)
        assert [policy.delay_before_retry(n, error) for n in range(5)] == [1, 2, 4, 5, 5]

    def test_gives_up_after_max_retries(self) -> None:
        assert POLICY.delay_before_retry(3, TransientLLMError("p", "x")) is None

    def test_retry_after_longer_than_backoff_is_honoured(self) -> None:
        assert POLICY.delay_before_retry(0, RateLimitError("p", retry_after=6)) == 6

    def test_retry_after_beyond_the_cap_is_not_waited_out(self) -> None:
        assert POLICY.delay_before_retry(0, RateLimitError("p", retry_after=60)) is None

    def test_rate_limit_without_hint_uses_backoff(self) -> None:
        assert POLICY.delay_before_retry(1, RateLimitError("p")) == 2


class TestRetryingProvider:
    def test_success_needs_no_wait(self) -> None:
        provider, waits = _retrying(Flaky())
        assert provider.complete(PROMPT) == "ok"
        assert waits == []

    def test_transient_errors_are_retried_with_growing_waits(self) -> None:
        inner = Flaky(TransientLLMError("p", "timeout"), TransientLLMError("p", "reset"))
        provider, waits = _retrying(inner)
        assert provider.complete(PROMPT) == "ok"
        assert waits == [1, 2]
        assert inner.calls == 3

    def test_rate_limit_is_retried(self) -> None:
        inner = Flaky(RateLimitError("p", retry_after=3))
        provider, waits = _retrying(inner)
        assert provider.complete(PROMPT) == "ok"
        assert waits == [3]

    def test_persistent_failure_raises_the_last_error_after_the_bound(self) -> None:
        errors = [TransientLLMError("p", f"attempt {n}") for n in range(10)]
        inner = Flaky(*errors)
        provider, waits = _retrying(inner)
        with pytest.raises(TransientLLMError, match="attempt 3"):
            provider.complete(PROMPT)
        assert inner.calls == 4
        assert waits == [1, 2, 4]

    def test_zero_retries_disables_retrying(self) -> None:
        inner = Flaky(TransientLLMError("p", "x"))
        provider, waits = _retrying(inner, RetryPolicy(0, 1.0, 8.0))
        with pytest.raises(TransientLLMError):
            provider.complete(PROMPT)
        assert inner.calls == 1
        assert waits == []

    @pytest.mark.parametrize(
        "error",
        [
            LLMResponseError("empty"),
            MissingAPIKeyError("openai", "OPENAI_API_KEY"),
            OllamaUnreachableError("http://localhost:11434"),
            LLMError("rejected"),
        ],
    )
    def test_other_errors_fail_immediately(self, error: LLMError) -> None:
        inner = Flaky(error)
        provider, waits = _retrying(inner)
        with pytest.raises(type(error)):
            provider.complete(PROMPT)
        assert inner.calls == 1
        assert waits == []

    def test_overlong_retry_after_fails_without_waiting(self) -> None:
        inner = Flaky(RateLimitError("p", retry_after=600))
        provider, waits = _retrying(inner)
        with pytest.raises(RateLimitError):
            provider.complete(PROMPT)
        assert waits == []

    def test_each_retry_is_announced(self) -> None:
        events: list[RetryEvent] = []
        inner = Flaky(RateLimitError("p"), TransientLLMError("p", "x"))
        provider = RetryingProvider(inner, POLICY, on_retry=events.append, sleep=lambda _: None)
        provider.complete(PROMPT)
        assert [(e.retry, e.max_retries, e.delay) for e in events] == [(1, 3, 1), (2, 3, 2)]
        assert isinstance(events[0].error, RateLimitError)

    def test_identity_comes_from_the_wrapped_provider(self) -> None:
        provider, _ = _retrying(Flaky())
        assert (provider.name, provider.model) == ("flaky", "flaky-1")


class TestFactoryWiring:
    def test_factory_returns_a_retrying_provider_with_configured_bounds(self) -> None:
        config = GitCommitAIConfig(
            llm_provider=ProviderName.OLLAMA,
            llm_max_retries=7,
            llm_retry_base_delay_seconds=0.5,
            llm_retry_max_delay_seconds=9,
        )
        provider = create_provider(config, env={})
        assert isinstance(provider, RetryingProvider)
        assert provider._policy == RetryPolicy(7, 0.5, 9)
        assert provider.name == "ollama"


def test_fake_llm_is_unaffected_by_retry_module() -> None:
    assert FakeLLM(["x"]).complete(PROMPT) == "x"
