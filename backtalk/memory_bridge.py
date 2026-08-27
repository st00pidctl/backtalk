"""Provider-neutral bridge from Backtalk to fullstack-agent portable memory."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from backtalk.vlog import log


def _runtime_path() -> Path | None:
    override = os.environ.get("BACKTALK_MEMORY_RUNTIME")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    agent_root = Path(os.environ.get("BACKTALK_AGENT_ROOT", Path.cwd()))
    candidates = (
        agent_root / "fullstack-agent" / "memory_runtime.py",
        agent_root / "memory_runtime.py",
        Path.home() / "universal-agent" / "fullstack-agent" / "memory_runtime.py",
    )
    return next((path for path in candidates if path.is_file()), None)


def _load_runtime():
    path = _runtime_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("portable_memory_runtime", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # memory_runtime imports sibling memory_engine.py.
    import sys
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec.loader.exec_module(module)
    return module


def pre_turn(utterance: str) -> dict[str, Any]:
    try:
        runtime = _load_runtime()
        if runtime is None:
            return {"prompt_context": "", "enabled": False}
        result = runtime.pre_turn(utterance).as_dict()
        result["enabled"] = True
        return result
    except Exception as exc:
        # Memory failure must not make the voice interface unavailable.
        log(f"[memory] pre-turn bridge degraded: {exc}")
        return {"prompt_context": "", "enabled": False, "error": str(exc)}


def post_turn(utterance: str, response: str) -> dict[str, Any]:
    try:
        runtime = _load_runtime()
        if runtime is None:
            return {"created_memory_ids": [], "enabled": False}
        result = runtime.post_turn(utterance, response)
        result["enabled"] = True
        created = result.get("created_memory_ids") or []
        if created:
            log(f"[memory] captured {len(created)} candidate(s)")
        immediate = result.get("immediate_review_ids") or []
        if immediate:
            log(f"[memory] {len(immediate)} candidate(s) require immediate review")
        return result
    except Exception as exc:
        log(f"[memory] post-turn bridge degraded: {exc}")
        return {"created_memory_ids": [], "enabled": False, "error": str(exc)}
