# backtalk: provider registry.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lazy core registry so optional providers do not import each other."""
from __future__ import annotations

from importlib import import_module

ALIASES = {
    "claude": "claude",
    "claude-code": "claude",
    "anthropic": "claude",
    "codex": "codex",
    "openai-codex": "codex",
    "openai": "codex",
    "generic": "generic-cli",
    "generic-cli": "generic-cli",
    "cli": "generic-cli",
}

_REGISTRY = {
    "claude": ("backtalk.core_claude", "ClaudeBrain"),
    "codex": ("backtalk.core_codex", "CodexBrain"),
    "generic-cli": ("backtalk.core_generic", "GenericCliBrain"),
}


def normalize_provider(provider: str | None) -> str:
    raw = (provider or "claude").strip().lower()
    return ALIASES.get(raw, raw)


def known_providers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def load_core_class(provider: str):
    provider = normalize_provider(provider)
    try:
        module_name, class_name = _REGISTRY[provider]
    except KeyError as exc:
        known = ", ".join(known_providers())
        raise ValueError(
            f"Unknown backtalk core {provider!r}. Known cores: {known}. "
            "Use generic-cli for an unlisted runtime."
        ) from exc
    module = import_module(module_name)
    return getattr(module, class_name)
