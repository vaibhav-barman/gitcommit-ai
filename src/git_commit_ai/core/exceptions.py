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
