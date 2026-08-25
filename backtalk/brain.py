# backtalk: provider-neutral brain facade.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compatibility facade for the voice loop.

`main.py` still imports WarmBrain. WarmBrain delegates to the runtime selected
by `core.provider`, or to an explicit drop-in class selected by `core.adapter`.
Audio and UI code never needs provider branches.
"""
from __future__ import annotations

import os

from backtalk.config import CFG
from backtalk.core_registry import load_core_class, normalize_provider

_CORE_CFG = CFG.get("core") or {}
PROVIDER = normalize_provider(_CORE_CFG.get("provider") or "claude")
_SESSION_SUFFIX = "" if PROVIDER == "claude" else f"_{PROVIDER.replace('-', '_')}"
SESSION_FILE = os.path.join(CFG["signals_dir"], f".backtalk_session{_SESSION_SUFFIX}")


class WarmBrain:
    """Stable Backtalk-facing API backed by a selected AgentCore."""

    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        cls = load_core_class(PROVIDER, adapter=_CORE_CFG.get("adapter"))
        selected_model = model
        if selected_model is None:
            if PROVIDER == "claude":
                selected_model = CFG.get("model")
            else:
                selected_model = _CORE_CFG.get("model") or None
        self._impl = cls(model=selected_model,
                         can_use_tool=can_use_tool,
                         resume_id=resume_id)
        if hasattr(self._impl, "session_file"):
            self._impl.session_file = SESSION_FILE

    @property
    def provider(self):
        return PROVIDER

    @property
    def provider_name(self):
        return getattr(self._impl, "display_name", PROVIDER)

    @property
    def capabilities(self):
        return getattr(self._impl, "capabilities", None)

    @property
    def model(self):
        return self._impl.model

    @model.setter
    def model(self, value):
        self._impl.model = value

    @property
    def session(self):
        return self._impl.session

    async def start(self):
        return await self._impl.start()

    async def ask_stream(self, utterance: str):
        async for item in self._impl.ask_stream(utterance):
            yield item

    async def interrupt(self):
        return await self._impl.interrupt()

    async def reset_turn(self, timeout: float = 8.0):
        return await self._impl.reset_turn(timeout=timeout)

    async def stop(self):
        return await self._impl.stop()

    async def command(self, cmd: str) -> str:
        return await self._impl.command(cmd)

    async def context_usage(self):
        return await self._impl.context_usage()

    async def set_permission_mode(self, backtalk_mode: str):
        return await self._impl.set_permission_mode(backtalk_mode)
