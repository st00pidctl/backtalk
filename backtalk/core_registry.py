# backtalk: provider registry.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lazy core registry with an explicit drop-in adapter escape hatch."""
from __future__ import annotations

from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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


def _load_explicit_adapter(adapter: str):
    """Load `module:Class` or `/path/to/file.py:Class` from local config."""
    if ":" not in adapter:
        raise ValueError(
            "core.adapter must be 'python.module:ClassName' or "
            "'/path/to/adapter.py:ClassName'"
        )
    source, class_name = adapter.rsplit(":", 1)
    source = source.strip()
    class_name = class_name.strip()
    if not source or not class_name:
        raise ValueError("core.adapter source and class name must both be set")

    path = Path(source).expanduser()
    if path.suffix == ".py" or path.exists():
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"Custom core adapter file not found: {path}")
        module_name = f"backtalk_dropin_{abs(hash(str(path)))}"
        spec = spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load custom core adapter: {path}")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = import_module(source)

    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ValueError(
            f"Adapter {source!r} has no class {class_name!r}"
        ) from exc

    for required in ("start", "ask_stream", "interrupt", "stop"):
        if not callable(getattr(cls, required, None)):
            raise ValueError(
                f"Custom core class {class_name!r} is missing required method {required}"
            )
    return cls


def load_core_class(provider: str, adapter: str | None = None):
    provider = normalize_provider(provider)
    if adapter:
        return _load_explicit_adapter(str(adapter))
    try:
        module_name, class_name = _REGISTRY[provider]
    except KeyError as exc:
        known = ", ".join(known_providers())
        raise ValueError(
            f"Unknown backtalk core {provider!r}. Known built-ins: {known}. "
            "Use generic-cli or set core.adapter for a drop-in Python adapter."
        ) from exc
    module = import_module(module_name)
    return getattr(module, class_name)
