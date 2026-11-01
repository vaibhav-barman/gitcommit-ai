"""Bounded exponential backoff around any LLM provider."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import LLMError, RateLimitError, TransientLLMError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from git_commit_ai.llm.base import LLMMessage, LLMProvider


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, and how long, to wait before retrying a failed call."""

    max_retries: int
    base_delay: float
    max_delay: float

    def delay_before_retry(self, retries_so_far: int, error: LLMError) -> float | None:
        """Return the wait before the next retry, or ``None`` to give up.

        The wait doubles with every retry up to ``max_delay``. A server's
        ``retry-after`` is honoured when it fits within ``max_delay``; one that
        asks for longer is not waited out, because the policy is a hard bound.
        """
        if retries_so_far >= self.max_retries:
            return None
        delay = min(self.max_delay, self.base_delay * 2.0**retries_so_far)
        if isinstance(error, RateLimitError) and error.retry_after is not None:
            if error.retry_after > self.max_delay:
                return None
            delay = max(delay, error.retry_after)
        return delay


@dataclass(frozen=True)
class RetryEvent:
    """Describes an imminent retry, for progress reporting."""

    provider: str
    retry: int
    max_retries: int
    delay: float
    error: LLMError


class RetryingProvider:
    """Retries rate-limited and transiently failing calls to ``inner``.

    Only ``RateLimitError`` and ``TransientLLMError`` are retried. Everything
    else (bad key, empty response, unreachable Ollama) fails immediately since
    repeating the request cannot help.

    Args:
        inner: The provider to protect.
        policy: Retry count and backoff bounds.
        on_retry: Called before each wait, e.g. to tell the user.
        sleep: Injected so tests do not actually wait.
    """

    def __init__(
        self,
        inner: LLMProvider,
        policy: RetryPolicy,
        on_retry: Callable[[RetryEvent], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._on_retry = on_retry
        self._sleep = sleep

    @property
    def name(self) -> str:
        """Name of the wrapped provider."""
        return self._inner.name

    @property
    def model(self) -> str:
        """Model of the wrapped provider."""
        return self._inner.model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Call the wrapped provider, retrying retryable failures."""
        retries = 0
        while True:
            try:
                return self._inner.complete(messages)
            except (RateLimitError, TransientLLMError) as exc:
                delay = self._policy.delay_before_retry(retries, exc)
                if delay is None:
                    raise
                retries += 1
                if self._on_retry is not None:
                    self._on_retry(
                        RetryEvent(self.name, retries, self._policy.max_retries, delay, exc)
                    )
                self._sleep(delay)
