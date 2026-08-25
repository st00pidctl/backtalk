# backtalk: provider-neutral core contract.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small contract implemented by every reasoning core.

The rest of backtalk owns audio, PTT, TTS, signaling, and the voice console.
A core only owns the conversation/runtime boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import AsyncIterator

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class CoreCapabilities:
    streaming: bool = False
    resume: bool = False
    interrupt: bool = True
    clear: bool = False
    compact: bool = False
    model_switch: bool = False
    effort: bool = False
    context_usage: bool = False
    spoken_tool_permissions: bool = False
    tools: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


class CoreError(RuntimeError):
    """A provider/runtime failure safe to surface to the voice loop."""


def sentences(text: str) -> list[str]:
    """Split model output into TTS-friendly sentences without losing tails."""
    text = " ".join((text or "").split()).strip()
    if not text:
        return []
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


class AgentCore:
    """Reference interface. Implementations may support more capabilities."""

    provider_id = "base"
    display_name = "Agent Core"
    capabilities = CoreCapabilities()

    model: str
    session: dict

    async def start(self) -> None:
        raise NotImplementedError

    async def ask_stream(self, utterance: str) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover

    async def interrupt(self) -> None:
        return None

    async def reset_turn(self, timeout: float = 8.0) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def command(self, cmd: str) -> str:
        return f"error: {self.display_name} does not implement {cmd}"

    async def context_usage(self):
        return None

    async def set_permission_mode(self, mode: str) -> None:
        return None
