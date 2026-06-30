"""Adapter registry — maps a cli name to its adapter class."""
from __future__ import annotations

from ..core import AgentSpec
from .base import Adapter
from .antigravity import AntigravityAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .grok import GrokAdapter

_REGISTRY: dict[str, type[Adapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "antigravity": AntigravityAdapter,
    "grok": GrokAdapter,
}

SUPPORTED_CLIS = tuple(_REGISTRY)


def get_adapter(spec: AgentSpec, login_path: str | None = None) -> Adapter:
    cls = _REGISTRY.get(spec.cli)
    if cls is None:
        raise ValueError(
            f"unknown cli {spec.cli!r} for agent {spec.name!r}; "
            f"supported: {', '.join(SUPPORTED_CLIS)}"
        )
    return cls(spec, login_path)


__all__ = ["Adapter", "get_adapter", "SUPPORTED_CLIS"]
