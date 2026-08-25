# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reference skeleton for a native drop-in Backtalk core adapter.

Copy this file outside the Backtalk repository, implement `_ask_runtime`, and
select it with:

    corectl.py use-custom my-runtime /path/to/my_core.py:MyRuntimeBrain
"""
from __future__ import annotations

from backtalk.core_base import AgentCore, CoreCapabilities, CoreError, sentences


class MyRuntimeBrain(AgentCore):
    provider_id = "my-runtime"
    display_name = "My Runtime"
    capabilities = CoreCapabilities(
        streaming=False,
        resume=False,
        interrupt=True,
        tools=True,
    )

    def __init__(self, model=None, can_use_tool=None, resume_id=None):
        self.model = model or "default"
        self.session = {
            "turns": 0,
            "out_tokens": 0,
            "in_tokens": 0,
            "cost": 0.0,
        }
        self._closed = False

    async def start(self):
        """Connect, validate credentials, or start a local runtime here."""
        return None

    async def _ask_runtime(self, utterance: str) -> str:
        """Replace with an SDK, daemon, API, or local-model call."""
        raise CoreError("Example adapter is a template. Implement _ask_runtime first.")

    async def ask_stream(self, utterance: str):
        if self._closed:
            raise CoreError("core is closed")
        text = await self._ask_runtime(utterance)
        self.session["turns"] += 1
        for sentence in sentences(text):
            yield sentence

    async def interrupt(self):
        """Cancel the active runtime request here when supported."""
        return None

    async def stop(self):
        self._closed = True
