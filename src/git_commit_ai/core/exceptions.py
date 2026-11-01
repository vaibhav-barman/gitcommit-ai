"""Application-specific exceptions for GitCommit AI."""


class GitCommitAIError(Exception):
    """Base exception for all GitCommit AI errors."""


class GitNotFoundError(GitCommitAIError):
    """Raised when the Git executable is not found on the system."""

    def __init__(self, message: str = "Git is not installed or not found in PATH") -> None:
        super().__init__(message)


class NotAGitRepositoryError(GitCommitAIError):
    """Raised when the current directory is not inside a Git repository."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Not a Git repository: {path}")
        self.path = path


class GitCommandError(GitCommitAIError):
    """Raised when a Git command fails with a non-zero exit code."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        cmd_str = " ".join(command)
        super().__init__(f"Git command failed (exit {returncode}): {cmd_str}\n{stderr}")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


class GitTimeoutError(GitCommitAIError):
    """Raised when a Git command exceeds the configured timeout."""

    def __init__(self, command: list[str], timeout: float) -> None:
        cmd_str = " ".join(command)
        super().__init__(f"Git command timed out after {timeout}s: {cmd_str}")
        self.command = command
        self.timeout = timeout


class EmptyRepositoryError(GitCommitAIError):
    """Raised when an operation requires commits but the repository has none."""

    def __init__(self, message: str = "Repository has no commits") -> None:
        super().__init__(message)


class ConfigurationError(GitCommitAIError):
    """Raised when configuration values or environment variables are invalid."""


class LLMError(GitCommitAIError):
    """Base exception for failures originating from an LLM provider."""


class MissingAPIKeyError(LLMError):
    """Raised when a provider requires an API key that is not set."""

    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__(
            f"No API key for provider '{provider}'. Set the {env_var} environment variable."
        )
        self.provider = provider
        self.env_var = env_var


class ProviderNotInstalledError(LLMError):
    """Raised when the SDK for a selected provider is not installed."""

    def __init__(self, provider: str, extra: str) -> None:
        super().__init__(
            f"The '{provider}' provider needs its SDK. "
            f"Install it with: pip install 'gitcommit-ai[{extra}]' "
            f"(installed with pipx? use: pipx install --force 'gitcommit-ai[{extra}]')"
        )
        self.provider = provider
        self.extra = extra


class OllamaUnreachableError(LLMError):
    """Raised when the Ollama server cannot be reached."""

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"Could not reach Ollama at {base_url}. Start it with 'ollama serve' "
            "or set GITCOMMIT_AI_OLLAMA_URL."
        )
        self.base_url = base_url


class RateLimitError(LLMError):
    """Raised when a provider rejects a request because of rate limiting."""

    def __init__(self, provider: str, retry_after: float | None = None) -> None:
        hint = f" Retry in about {retry_after:.0f}s." if retry_after is not None else ""
        super().__init__(f"Provider '{provider}' rate limit exceeded.{hint}")
        self.provider = provider
        self.retry_after = retry_after


class TransientLLMError(LLMError):
    """Raised for failures that usually clear up on retry: timeouts, dropped
    connections, and server-side (5xx) errors."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"Temporary failure talking to {provider}: {detail}")
        self.provider = provider


class LLMResponseError(LLMError):
    """Raised when a provider returns an empty, malformed, or unusable response."""


class AgentStateError(GitCommitAIError):
    """Raised when a graph node runs before the data it depends on exists."""

    def __init__(self, missing: str) -> None:
        super().__init__(f"Agent state is missing '{missing}'; an upstream node did not run")
        self.missing = missing


class StaleStagingError(GitCommitAIError):
    """Raised when the staged changes differ from those the message was written for."""

    def __init__(self) -> None:
        super().__init__(
            "The staged changes changed while the message was being generated, "
            "so nothing was committed. Run the command again."
        )


class HookError(GitCommitAIError):
    """Raised when a Git hook cannot be installed or removed safely."""
