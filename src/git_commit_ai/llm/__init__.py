"""LLM provider abstraction."""

from git_commit_ai.llm.base import LLMMessage, LLMProvider, Role
from git_commit_ai.llm.fake import FakeLLM

__all__ = ["FakeLLM", "LLMMessage", "LLMProvider", "Role"]
